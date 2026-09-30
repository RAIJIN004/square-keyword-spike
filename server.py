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
import json
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
                # normalizar campos (subTitle trae el texto largo en BUZZ_LONG)
                content = p.get("content") or p.get("body") or p.get("text") or ""
                title = p.get("title") or ""
                subtitle = p.get("subTitle") or ""
                full_text = f"{title} {subtitle} {content}".strip()
                tps = p.get("tradingPairsV2") or p.get("tradingPairs") or []
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
                    "trading_pairs": tps if isinstance(tps, list) else [],
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

    # 2. Trending (type=1) - lo caliente, detecta foco temprano
    for page in range(1, 6):
        try:
            r = session.get(API_ARTICLE, params={"pageIndex": page, "pageSize": page_size, "type": 1}, timeout=12)
            if r.status_code != 200:
                break
            data = r.json()
            lst = data.get("data", {}).get("vos") or data.get("data", {}).get("list") or []
            if not lst:
                break
            new = 0
            for p in lst:
                pid = str(p.get("id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                content = p.get("content") or ""
                title = p.get("title") or ""
                subtitle = p.get("subTitle") or ""
                tps3 = p.get("tradingPairsV2") or p.get("tradingPairs") or []
                posts.append({
                    "id": pid,
                    "text": f"{title} {subtitle} {content}".strip(),
                    "hashtags": p.get("hashtagList") or [],
                    "trading_pairs": tps3 if isinstance(tps3, list) else [],
                    "timestamp": p.get("date") or 0,
                    "viewCount": p.get("viewCount", 0),
                    "likeCount": p.get("likeCount", 0),
                })
                new += 1
            if new == 0:
                break
            time.sleep(0.5)
        except Exception:
            break

    # 3. News feed como complemento (primeras 5 páginas)
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
                subtitle = p.get("subTitle") or ""
                tps2 = p.get("tradingPairsV2") or p.get("tradingPairs") or []
                posts.append({
                    "id": pid,
                    "text": f"{title} {subtitle} {content}".strip(),
                    "hashtags": p.get("hashtagList") or [],
                    "trading_pairs": tps2 if isinstance(tps2, list) else [],
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

import os as _os
STATE_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "square_baseline.json")

def load_state() -> Dict[str, Any]:
    """Baseline persistente: conteos por moneda por hora de corridas anteriores."""
    try:
        if _os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"hourly": {}}

def save_state(hourly: Dict[str, Dict[str, int]], keep_hours: int = 72) -> None:
    """Fusiona conteos actuales y poda a keep_hours."""
    try:
        st = load_state()
        agg = defaultdict(Counter)
        for h, counts in st.get("hourly", {}).items():
            agg[h].update(counts)
        for h, counts in hourly.items():
            agg[h].update(counts)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=keep_hours)).isoformat()
        pruned = {h: dict(c) for h, c in agg.items() if h >= cutoff}
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"hourly": pruned, "updated": datetime.now(timezone.utc).isoformat()}, f)
    except Exception:
        pass

CASHTAG_RE = re.compile(r"\$([A-Z]{2,12})\b")
# tokens comunes que NO son monedas (evitan ruido)
STOP_COINS = {"US", "USA", "ETF", "AI", "IT", "ON", "GO", "UP", "TV", "CEO", "ATM", "VIP", "NFT", "DEFI"}
# stablecoins y acciones (ruido para trading de momentum: se excluyen con exclude_noise=True)
NOISE_COINS = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "T", "MU", "NVDA", "AAPL", "TSLA",
               "GOOGL", "ARM", "DELL", "HOODB", "MSTR", "COIN", "AMD", "INTC", "META", "AMZN"}

def extract_coins_from_post(text: str, hashtags: List[str], trading_pairs: List[Dict]) -> List[str]:
    """Extrae códigos de moneda de un post: tradingPairsV2.code + $CASHTAG + #hashtag."""
    found = set()
    for t in (trading_pairs or []):
        code = str(t.get("code", "")).upper().strip()
        # quitar sufijos tipo .US (stocks) y prefijos numéricos (1000FLOKI)
        code = re.sub(r"\.US$", "", code)
        code = re.sub(r"^\d+", "", code)
        if 2 <= len(code) <= 12 and code not in STOP_COINS:
            found.add(code)
    tl = text or ""
    for m in CASHTAG_RE.findall(tl):
        c = re.sub(r"^\d+", "", m.upper())
        if 2 <= len(c) <= 12 and c not in STOP_COINS:
            found.add(c)
    for h in (hashtags or []):
        c = re.sub(r"^\d+", "", normalize_text(str(h)).lstrip("#$").upper())
        if 2 <= len(c) <= 12 and c not in STOP_COINS and re.fullmatch(r"[A-Z0-9]+", c or ""):
            found.add(c)
    return sorted(found)

def get_24h_change(symbol_usdt: str, session: requests.Session) -> Dict[str, Any]:
    """Precio + cambio 24h vía API pública de Binance (sin key)."""
    try:
        r = session.get("https://api.binance.com/api/v3/ticker/24hr",
                        params={"symbol": symbol_usdt}, timeout=8)
        if r.status_code == 200:
            d = r.json()
            return {"price": float(d["lastPrice"]), "chg_24h": float(d["priceChangePercent"]),
                    "quote_vol": float(d["quoteVolume"])}
    except Exception:
        pass
    return {}

@mcp.tool()
def top_rumor_discovery(
    spike_window_minutes: int = 90,
    baseline_hours: int = 12,
    max_pages: int = 30,
    top_n: int = 10,
    min_total_mentions: int = 3,
    with_price: bool = True,
    short_window_minutes: int = 30,
    use_persistent_baseline: bool = True,
    tradeable_only: bool = True,
    exclude_noise: bool = True,
) -> dict:
    """
    DESCUBRIMIENTO GENERAL: top de monedas con pico irregular de menciones en Square,
    sin listas fijas. Extrae todas las monedas ($CASHTAG, tradingPairs, hashtags),
    compara ventana actual vs baseline (z-score Poisson, funciona con conteos bajos),
    y cruza con precio/cambio 24h de Binance para rankear.

    Args:
        spike_window_minutes: Ventana actual (default 90).
        baseline_hours: Horas de baseline (default 12).
        max_pages: Páginas de Square a scrapear (default 30, ~600 posts).
        top_n: Cuántas devolver (default 10).
        min_total_mentions: Mínimo de menciones totales para considerar la moneda (default 3).
        with_price: Si True, cruza con ticker 24h público de Binance.

    Returns:
        Ranking con z-score, menciones recientes vs esperadas, posts ejemplo, precio y cambio 24h.
    """
    posts = fetch_square_posts(max_pages=max_pages)
    if not posts:
        return {"error": "Square no devolvió posts (API bloqueada o sin datos). Reintenta."}

    # construir conteo horario del fetch actual
    now = datetime.now(timezone.utc)
    spike_start = now - timedelta(minutes=spike_window_minutes)
    baseline_start = now - timedelta(hours=baseline_hours)
    short_start = now - timedelta(minutes=short_window_minutes)

    current_hourly = defaultdict(Counter)
    for p in posts:
        dt = p.get("datetime")
        if not dt or dt < baseline_start:
            continue
        coins = extract_coins_from_post(p.get("text", ""), p.get("hashtags", []), p.get("trading_pairs", []))
        if not coins:
            continue
        hk = dt.replace(minute=0, second=0, microsecond=0).isoformat()
        for c in coins:
            current_hourly[hk][c] += 1

    # fusionar baseline persistente (corridas anteriores) para detectar MÁS TEMPRANO
    persistent_note = "solo-fetch"
    if use_persistent_baseline:
        st = load_state()
        for h, counts in st.get("hourly", {}).items():
            if h >= baseline_start.isoformat() and h not in current_hourly:
                current_hourly[h] = Counter(counts)
            elif h >= baseline_start.isoformat():
                for c, n in counts.items():
                    current_hourly[h][c] = max(current_hourly[h][c], n)
        save_state({h: dict(c) for h, c in current_hourly.items()})
        persistent_note = "fetch+persistente(%d horas guardadas)" % len(st.get("hourly", {}))

    # reutilizar conteo fusionado (fetch + persistente)
    hourly = current_hourly
    coin_posts_recent = defaultdict(list)  # coin -> posts en ventana spike
    coin_posts_short = defaultdict(list)  # coin -> posts en ventana corta (detección temprana)

    for p in posts:
        dt = p.get("datetime")
        if not dt or dt < baseline_start:
            continue
        text = p.get("text", "")
        coins = extract_coins_from_post(text, p.get("hashtags", []), p.get("trading_pairs", []))
        for c in coins:
            if dt >= spike_start and len(coin_posts_recent[c]) < 3:
                coin_posts_recent[c].append({
                    "text": text[:220].replace("\n", " "),
                    "time": dt.isoformat(),
                    "views": p.get("viewCount"),
                })
            if dt >= short_start and len(coin_posts_short[c]) < 2:
                coin_posts_short[c].append({
                    "text": text[:180].replace("\n", " "),
                    "time": dt.isoformat(),
                })

    hours_sorted = sorted(hourly.keys())
    # última hora completa + fracción actual: usar ventana spike como tasa
    window_h = spike_window_minutes / 60.0

    # Totales por hora (todas las monedas) para el SHARE TRANSVERSAL:
    # ¿qué % de la conversación total se lleva cada moneda ahora vs su % normal?
    total_per_hour = {h: sum(hourly[h].values()) for h in hours_sorted}
    spike_hours = set()
    for h in hours_sorted:
        try:
            if datetime.fromisoformat(h) >= spike_start - timedelta(hours=1):
                spike_hours.add(h)
        except Exception:
            pass
    total_recent_all = sum(total_per_hour[h] for h in spike_hours) or 1
    total_base_all = sum(v for h, v in total_per_hour.items() if h not in spike_hours) or 1
    n_base_hours = max(len([h for h in hours_sorted if h not in spike_hours]), 1)

    session = requests.Session()
    session.headers.update(HEADERS)
    ranked = []
    totals = Counter()
    for h in hours_sorted:
        totals.update(hourly[h])

    for coin, total in totals.items():
        if total < min_total_mentions:
            continue
        if exclude_noise and coin in NOISE_COINS:
            continue
        # serie horaria excluyendo la ventana actual (aprox: excluir última hora si spike<=60, si no prorratear)
        series = []
        for h in hours_sorted:
            # parsear hora
            try:
                hh = datetime.fromisoformat(h)
            except Exception:
                continue
            if hh >= spike_start - timedelta(hours=1):
                continue
            series.append(hourly[h][coin])
        if not series:
            series = [0]
        mean_h = statistics.mean(series)
        # Poisson z: (actual - esperado) / sqrt(esperado + 1) — funciona con conteos bajos
        # contar menciones en ventana spike directamente:
        recent = 0
        for h in hours_sorted:
            try:
                hh = datetime.fromisoformat(h)
            except Exception:
                continue
            if hh >= spike_start - timedelta(hours=1):
                recent += hourly[h][coin]
        expected = mean_h * window_h
        z = (recent - expected) / math.sqrt(expected + 1)
        # ratio velocidad
        ratio = recent / (expected + 0.5)
        # SHARE TRANSVERSAL: % de la conversación total ahora vs % normal
        base_coin = sum(hourly[h][coin] for h in hours_sorted if h not in spike_hours)
        share_recent = (recent / total_recent_all) * 100
        share_base = (base_coin / total_base_all) * 100
        share_ratio = share_recent / max(share_base, 0.5)  # suelo 0.5% para no explotar con baseline 0
        is_share_spike = bool(share_ratio >= 3.0 and recent >= 2)

        item = {
            "coin": coin,
            "total_mentions": total,
            "recent_mentions": recent,
            "expected_in_window": round(expected, 2),
            "baseline_per_hour": round(mean_h, 2),
            "z_poisson": round(z, 2),
            "velocity_ratio": round(ratio, 2),
            "share_recent_pct": round(share_recent, 2),
            "share_baseline_pct": round(share_base, 2),
            "share_ratio": round(share_ratio, 2),
            "is_spike": bool(z >= 2.0 and recent > 0),
            "is_warming": bool(1.0 <= z < 2.0 and recent > 0),
            "is_share_spike": is_share_spike,
        }
        if with_price:
            for suffix in ("USDT",):
                px = get_24h_change(f"{coin}{suffix}", session)
                if px:
                    item["price"] = px["price"]
                    item["chg_24h"] = px["chg_24h"]
                    item["quote_vol_24h"] = round(px["quote_vol"], 0)
                    break
        # BONUS RUMOR: si los posts recientes traen keywords rumor = leading, no solo atención
        rumor_hits = []
        for ex in coin_posts_recent.get(coin, []):
            tl = (ex.get("text") or "").lower()
            for kw in DEFAULT_RUMOR_KEYWORDS:
                if kw in tl and kw not in rumor_hits:
                    rumor_hits.append(kw)
        item["rumor_keywords"] = rumor_hits[:5]
        rumor_bonus = min(len(rumor_hits) * 0.5, 1.5)
        # score ranking: z temporal + bonus transversal (log, cap 2) + rumor + momentum precio
        mom = abs(item.get("chg_24h", 0))
        share_bonus = min(math.log1p(max(share_ratio - 1, 0)), 2.0) if recent >= 2 else 0.0
        item["rank_score"] = round(z * (1 + min(mom, 20) / 20) + share_bonus + rumor_bonus, 2)
        item["examples"] = coin_posts_recent.get(coin, [])[:2]
        # DETECCIÓN TEMPRANA: aceleración en ventana corta (últimos short_window_minutes)
        early = 0
        for h in hours_sorted:
            try:
                hh = datetime.fromisoformat(h)
            except Exception:
                continue
            if hh >= short_start - timedelta(hours=1):
                early += hourly[h][coin]
        early_expected = mean_h * (short_window_minutes / 60.0)
        early_z = (early - early_expected) / math.sqrt(early_expected + 1)
        item["early_mentions"] = early
        item["early_z"] = round(early_z, 2)
        item["is_early_burst"] = bool(early_z >= 2.0 and early >= 2)
        item["early_examples"] = coin_posts_short.get(coin, [])[:2]
        ranked.append(item)

    if tradeable_only:
        # solo monedas con par USDT real en Binance (las demás son ruido/fragmentos)
        ranked = [r for r in ranked if r.get("price") is not None]
    ranked.sort(key=lambda x: (x["rank_score"], x["recent_mentions"]), reverse=True)
    spikes = [r for r in ranked if r["is_spike"]]
    warming = [r for r in ranked if r["is_warming"]]
    early_list = [r for r in ranked if r.get("is_early_burst")]
    return {
        "posts_fetched": len(posts),
        "baseline_mode": persistent_note,
        "window": f"{spike_window_minutes}m vs baseline {baseline_hours}h + early {short_window_minutes}m",
        "coins_tracked": len(ranked),
        "spikes_detected": len(spikes),
        "warming_up": len(warming),
        "early_bursts": [{"coin": r["coin"], "early_z": r["early_z"], "early_mentions": r["early_mentions"],
                          "chg_24h": r.get("chg_24h"), "examples": r["early_examples"]} for r in early_list[:top_n]],
        "top": ranked[:top_n],
        "note": ("z_poisson>=2 = pico irregular (funciona con pocas menciones). "
                 "warming = acelerando (1-2). Cruza rank_score con chg_24h: spike + momentum = candidato Hermes. "
                 "Si el top ya está lleno de posts con mismos niveles = techo de atención, NO entrar (regla BE)."),
    }

if __name__ == "__main__":
    mcp.run()
