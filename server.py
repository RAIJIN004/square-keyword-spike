"""MCP Square Keyword Spike - detecta picos irregulares de palabras clave correlacionadas a una moneda.

Idea: traders con cashtag $BTC opinan DESPUES del movimiento (lagging).
Keywords rumor ("listing", "partnership") aparecen ANTES (leading). Este MCP encuentra
cuando una keyword hace un pico irregular y está correlacionada a una moneda específica.

Usa endpoints públicos de Binance Square (sin API key) documentados en
wxie0815-arch/binance-square-monitor:
- https://www.binance.com/bapi/composite/v3/friendly/pgc/content/article/list?type=2
- https://www.binance.com/bapi/composite/v4/friendly/pgc/feed/news/list
"""
import time
import re
import math
import statistics
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "square-keyword-spike",
    description="Detecta picos irregulares de keywords correlacionadas a una moneda en Binance Square. Leading indicator para rumor diffusion.",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.binance.com/en/square",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}

API_ARTICLE = "https://www.binance.com/bapi/composite/v3/friendly/pgc/content/article/list"
API_NEWS = "https://www.binance.com/bapi/composite/v4/friendly/pgc/feed/news/list"

# keywords rumor típicas por defecto
DEFAULT_RUMOR_KEYWORDS = [
    "listing", "list", "binance listing", "coinbase listing",
    "partnership", "partner", "collab",
    "unlock", "vesting", "airdrop", "burn", "buyback",
    "hack", "exploit", "sec", "etf", "approval",
    "whale", "accumulation", "insider",
]

def fetch_square_posts(max_pages: int = 25, page_size: int = 20) -> List[Dict[str, Any]]:
    """Trae posts de Square de últimas ~48h combinando Latest + News feed."""
    posts = []
    seen = set()
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Latest (type=2) - cronológico 42h
    for page in range(1, max_pages + 1):
        try:
            r = session.get(API_ARTICLE, params={"pageIndex": page, "pageSize": page_size, "type": 2}, timeout=12)
            if r.status_code != 200:
                break
            data = r.json()
            # estructura: data.data.list o similar
            lst = data.get("data", {}).get("vos") or data.get("data", {}).get("list") or data.get("data", {}).get("data") or []
            if not lst:
                lst = data.get("data", [])
                if isinstance(lst, dict):
                    lst = lst.get("list", []) or lst.get("vos", [])
            if not lst:
                break
            new = 0
            for p in lst:
                pid = str(p.get("id") or p.get("postId") or p.get("contentId") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                # normalizar campos
                content = p.get("content") or p.get("body") or p.get("text") or ""
                title = p.get("title") or ""
                full_text = f"{title} {content}".strip()
                # fecha: puede venir en ms
                ts = p.get("date") or p.get("publishTime") or p.get("createTime") or 0
                # hashtags
                hashtags = p.get("hashtagList") or p.get("hashtags") or []
                if isinstance(hashtags, list):
                    hashtags = [h if isinstance(h, str) else str(h) for h in hashtags]
                posts.append({
                    "id": pid,
                    "text": full_text,
                    "hashtags": hashtags,
                    "timestamp": ts,
                    "viewCount": p.get("viewCount", 0),
                    "likeCount": p.get("likeCount", 0),
                })
                new += 1
            if new == 0:
                break
            time.sleep(0.5)
        except Exception as e:
            # print(f"fetch error page {page}: {e}")
            break

    # 2. News feed como complemento (primeras 5 páginas)
    for page in range(1, 6):
        try:
            r = session.get(API_NEWS, params={"pageIndex": page, "pageSize": page_size}, timeout=12)
            if r.status_code != 200:
                break
            data = r.json()
            lst = data.get("data", {}).get("vos") or data.get("data", {}).get("list") or []
            if not lst:
                break
            for p in lst:
                pid = str(p.get("id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                content = p.get("content") or ""
                title = p.get("title") or ""
                posts.append({
                    "id": pid,
                    "text": f"{title} {content}".strip(),
                    "hashtags": p.get("hashtagList") or [],
                    "timestamp": p.get("date") or 0,
                    "viewCount": p.get("viewCount", 0),
                    "likeCount": p.get("likeCount", 0),
                })
            time.sleep(0.5)
        except:
            break

    # normalizar timestamp a ms -> datetime
    for p in posts:
        ts = p["timestamp"]
        # si viene en segundos ( < 1e12 ), convertir a ms
        try:
            ts = int(ts)
            if ts > 0 and ts < 1e12:
                ts = ts * 1000
            p["ts_ms"] = ts
            p["datetime"] = datetime.fromtimestamp(ts/1000, tz=timezone.utc) if ts else None
        except:
            p["ts_ms"] = 0
            p["datetime"] = None

    return posts

def normalize_text(t: str) -> str:
    return t.lower() if t else ""

def contains_coin(text: str, hashtags: List[str], coin: str) -> bool:
    """True si el post menciona la moneda (cashtag $COIN, hashtag, o texto)."""
    coin_u = coin.upper().strip().lstrip("$#")
    coin_l = coin_u.lower()
    text_l = normalize_text(text)
    # cashtag $COIN
    if f"${coin_l}" in text_l or f"#{coin_l}" in text_l:
        return True
    # texto contiene coin
    if coin_l in text_l:
        # evitar falsos positivos de 2 letras (ej "AI"), exigir word boundary
        if len(coin_l) <= 2:
            if re.search(rf"\b{re.escape(coin_l)}\b", text_l):
                return True
        else:
            return True
    # hashtags lista
    for h in hashtags:
        if normalize_text(str(h)).lstrip("#$") == coin_l:
            return True
    return False

def contains_keyword(text: str, keyword: str) -> bool:
    kw = keyword.lower().strip()
    tl = normalize_text(text)
    # frase compuesta
    return kw in tl

@mcp.tool()
def detect_keyword_spike(
    coin: str = "BTC",
    keywords: List[str] = None,
    spike_window_minutes: int = 60,
    baseline_hours: int = 24,
    z_threshold: float = 2.0,
    max_pages: int = 20,
) -> dict:
    """
    Detecta picos irregulares de palabras clave correlacionadas a una moneda en Binance Square.

    No mide cashtag $COIN (lagging), mide keywords rumor (leading) que hacen spike
    y coincide con mención de la moneda.

    Args:
        coin: Moneda a correlacionar (ej: BTC, SOL, ASTER, PEPE). Se busca $COIN, #COIN y texto.
        keywords: Lista de keywords rumor a vigilar. Si None usa lista por defecto (listing, partnership, unlock...).
        spike_window_minutes: Ventana actual para detectar spike (default 60 = última hora).
        baseline_hours: Horas de baseline para media/desvío (default 24).
        z_threshold: Z-score mínimo para considerar pico irregular (default 2.0).
        max_pages: Páginas de Square a scrapear (default 20, ~400 posts, ~24-42h).

    Returns:
        Dict con baseline, ventana actual, picos detectados, correlación coin+keyword.
    """
    if not keywords:
        keywords = DEFAULT_RUMOR_KEYWORDS
    # normalizar keywords
    keywords = [k.strip() for k in keywords if k.strip()]
    coin = coin.strip().upper().lstrip("$#")

    posts = fetch_square_posts(max_pages=max_pages)
    if not posts:
        return {
            "error": "No se pudieron obtener posts de Square (API bloqueada o sin datos). Prueba con menos páginas o reintenta.",
            "coin": coin,
            "keywords": keywords,
            "posts_fetched": 0,
        }

    now = datetime.now(timezone.utc)
    baseline_start = now - timedelta(hours=baseline_hours)
    spike_start = now - timedelta(minutes=spike_window_minutes)

    # buckets por hora para baseline
    hourly_counts = defaultdict(lambda: Counter())  # hour -> keyword -> count (solo posts que también mencionan coin)
    hourly_total = Counter()  # hour -> total posts con coin

    spike_counts = Counter()  # keyword -> count en ventana spike con coin
    spike_total_coin = 0
    current_window_posts = []

    # también conteo global keyword sin coin para contexto
    spike_counts_global = Counter()

    for p in posts:
        dt = p.get("datetime")
        if not dt:
            continue
        text = p.get("text", "")
        has_coin = contains_coin(text, p.get("hashtags", []), coin)

        # contar en baseline
        if dt >= baseline_start:
            hour_key = dt.replace(minute=0, second=0, microsecond=0).isoformat()
            if has_coin:
                hourly_total[hour_key] += 1
                for kw in keywords:
                    if contains_keyword(text, kw):
                        hourly_counts[hour_key][kw] += 1

        # ventana spike
        if dt >= spike_start:
            # global
            for kw in keywords:
                if contains_keyword(text, kw):
                    spike_counts_global[kw] += 1
            if has_coin:
                spike_total_coin += 1
                current_window_posts.append({
                    "id": p["id"],
                    "text": p["text"][:280],
                    "time": dt.isoformat(),
                    "views": p.get("viewCount"),
                })
                for kw in keywords:
                    if contains_keyword(text, kw):
                        spike_counts[kw] += 1

    # calcular stats por keyword
    results = []
    for kw in keywords:
        # serie horaria para este keyword con coin
        series = [hourly_counts[h][kw] for h in sorted(hourly_counts.keys())]
        if not series:
            series = [0]
        mean = statistics.mean(series) if series else 0
        stdev = statistics.stdev(series) if len(series) > 1 else (math.sqrt(mean) if mean > 0 else 1.0)
        if stdev == 0:
            stdev = 1.0
        current = spike_counts.get(kw, 0)
        # normalizar current a tasa horaria (spike_window 60min = 1h, si 30min ajustar)
        # baseline es por hora, current es por spike_window. Ajustar:
        expected_per_window = mean * (spike_window_minutes / 60.0)
        stdev_per_window = stdev * math.sqrt(spike_window_minutes / 60.0) if spike_window_minutes != 60 else stdev
        if stdev_per_window == 0:
            stdev_per_window = 1.0
        z = (current - expected_per_window) / stdev_per_window if stdev_per_window else 0

        # correlación: % de menciones de keyword que también mencionan coin en ventana
        global_kw = spike_counts_global.get(kw, 0)
        corr = (current / global_kw * 100) if global_kw > 0 else (100 if current > 0 else 0)

        is_spike = z >= z_threshold and current > 0

        results.append({
            "keyword": kw,
            "current_with_coin": current,
            "global_mentions": global_kw,
            "correlation_pct": round(corr, 1),
            "baseline_mean_per_hour": round(mean, 2),
            "baseline_stdev": round(stdev, 2),
            "expected_in_window": round(expected_per_window, 2),
            "z_score": round(z, 2),
            "is_irregular_spike": is_spike,
        })

    # ordenar por z
    results.sort(key=lambda x: x["z_score"], reverse=True)
    spikes = [r for r in results if r["is_irregular_spike"]]

    # resumen
    total_posts = len(posts)
    return {
        "coin": coin,
        "spike_window_minutes": spike_window_minutes,
        "baseline_hours": baseline_hours,
        "z_threshold": z_threshold,
        "posts_fetched": total_posts,
        "posts_with_coin_in_baseline": sum(hourly_total.values()),
        "posts_with_coin_in_spike_window": spike_total_coin,
        "spike_summary": f"{len(spikes)} picos irregulares de {len(keywords)} keywords para ${coin} en últimos {spike_window_minutes}m",
        "all_keywords": results,
        "spikes_detected": spikes,
        "top_example_posts_with_coin": current_window_posts[:5],
        "interpretation": (
            "Leading indicator: si hay spike con correlación alta (>60%) y z>2, es difusión de rumor ANTES del movimiento. "
            "Cashtag solo ($COIN) es lagging (traders opinan después). "
            "Usa Jev con question noul '¿este rumor moverá precio?' para probabilidad calibrada."
            if spikes else "Sin picos irregulares en ventana actual. Baseline normal."
        ),
    }

@mcp.tool()
def scan_all_rumors(
    coins: List[str] = None,
    keywords: List[str] = None,
    spike_window_minutes: int = 60,
) -> dict:
    """
    Escanea múltiples monedas a la vez para encontrar qué keyword está haciendo spike en cuál moneda.
    Útil para descubrir la correlación keyword->coin sin saberla antes.
    Optimizado: hace un solo fetch y reutiliza posts.
    """
    if not coins:
        coins = ["BTC", "ETH", "SOL", "ASTER", "PEPE", "XRP"]
    if not keywords:
        keywords = ["listing", "partnership", "unlock", "airdrop", "hack", "etf"]

    # fetch una sola vez
    posts = fetch_square_posts(max_pages=15)
    if not posts:
        return {"error": "No se pudieron obtener posts de Square", "scanned_coins": coins}

    now = datetime.now(timezone.utc)
    spike_start = now - timedelta(minutes=spike_window_minutes)
    baseline_start = now - timedelta(hours=12)

    all_spikes = []
    for coin in coins:
        coin_u = coin.upper().lstrip("$#")
        # contar rápido reutilizando posts
        spike_counts = Counter()
        spike_counts_global = Counter()
        hourly_counts = defaultdict(lambda: Counter())
        spike_total = 0
        for p in posts:
            dt = p.get("datetime")
            if not dt:
                continue
            text = p.get("text","")
            has_coin = contains_coin(text, p.get("hashtags",[]), coin_u)
            if dt >= baseline_start:
                hk = dt.replace(minute=0, second=0, microsecond=0).isoformat()
                if has_coin:
                    for kw in keywords:
                        if contains_keyword(text, kw):
                            hourly_counts[hk][kw] += 1
            if dt >= spike_start:
                for kw in keywords:
                    if contains_keyword(text, kw):
                        spike_counts_global[kw] += 1
                if has_coin:
                    for kw in keywords:
                        if contains_keyword(text, kw):
                            spike_counts[kw] += 1
        for kw in keywords:
            series = [hourly_counts[h][kw] for h in sorted(hourly_counts.keys())] or [0]
            mean = statistics.mean(series) if series else 0
            stdev = statistics.stdev(series) if len(series)>1 else (math.sqrt(mean) if mean>0 else 1.0)
            if stdev==0: stdev=1.0
            cur = spike_counts.get(kw,0)
            exp = mean * (spike_window_minutes/60.0)
            sdw = stdev * math.sqrt(spike_window_minutes/60.0) if spike_window_minutes!=60 else stdev
            if sdw==0: sdw=1.0
            z = (cur - exp)/sdw if sdw else 0
            if z >= 2.0 and cur>0:
                corr = (cur / spike_counts_global[kw] *100) if spike_counts_global[kw]>0 else 100
                all_spikes.append({"coin": coin_u, "keyword": kw, "z": round(z,2), "current": cur, "corr": round(corr,1)})
    all_spikes.sort(key=lambda x: x["z"], reverse=True)
    return {
        "scanned_coins": coins,
        "keywords": keywords,
        "window": f"{spike_window_minutes}m",
        "posts_fetched": len(posts),
        "ranking_spikes": all_spikes,
        "top": all_spikes[:5],
        "note": "z>=2 = pico irregular. corr% = qué % de menciones globales de esa keyword están atadas a esa moneda en la ventana."
    }

if __name__ == "__main__":
    mcp.run()
