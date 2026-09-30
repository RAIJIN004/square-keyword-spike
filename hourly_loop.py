"""Loop horario Hermes: abre 1 posicion/hora en REAL si hay campo, mismo criterio.
Criterio: unified TOP + Square silencioso (<=1 mencion 12h) + flow no opuesto + BTC a favor.
Seguridad: max 2 posiciones abiertas, disponible > 1.3 USDT, notional ~11 USDT, 10x,
TP -5% / SL +7% (SHORT) con closePosition. Log en hourly_log.txt.
"""
import sys, json, time, hmac, hashlib, urllib.parse, requests
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\jhonv\Downloads\trend-finder-unified")
sys.path.insert(0, r"C:\Users\jhonv\Downloads\square-keyword-spike")

BASE = "https://fapi.binance.com"
cfg = json.load(open(r"C:\Users\jhonv\.opencode\opencode.json"))
env = cfg["mcp"]["binance-futures-real"]["environment"]
KEY, SEC = env["BINANCE_API_KEY"], env["BINANCE_API_SECRET"]

TIME_OFFSET = 0

def sync_clock():
    global TIME_OFFSET
    try:
        t = requests.get(BASE + "/fapi/v1/time", timeout=10).json()["serverTime"]
        TIME_OFFSET = t - int(time.time() * 1000)
        log(f"reloj sincronizado offset={TIME_OFFSET}ms")
    except Exception as e:
        log(f"no se pudo sincronizar reloj: {e}")

def signed(method, path, params=None):
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000) + TIME_OFFSET
    params["recvWindow"] = 60000
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = requests.request(method, BASE + path, params=qs + f"&signature={sig}",
                         headers={"X-MBX-APIKEY": KEY}, timeout=15)
    return r.json()

def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, flush=True)
    with open(r"C:\Users\jhonv\Downloads\square-keyword-spike\hourly_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def filters(sym):
    info = requests.get(BASE + "/fapi/v1/exchangeInfo", params={"symbol": sym}, timeout=15).json()
    s = info["symbols"][0]
    tick = step = minqty = None
    for f in s["filters"]:
        if f["filterType"] == "PRICE_FILTER": tick = float(f["tickSize"])
        if f["filterType"] == "LOT_SIZE":
            step, minqty = float(f["stepSize"]), float(f["minQty"])
    return tick, step, minqty

def rnd_step(x, step):
    import math
    return math.floor(x / step) * step if step else x

def cycle(n):
    from unified import scan, flow_bias
    from server import fetch_square_posts, extract_coins_from_post
    from collections import Counter
    from datetime import timedelta

    acct = signed("GET", "/fapi/v2/account")
    avail = float(acct.get("availableBalance", 0))
    held = [p["symbol"] for p in acct.get("positions", []) if abs(float(p.get("positionAmt", 0))) > 0]
    log(f"ciclo {n}: disponible={avail:.2f} abiertas={held}")
    if len(held) >= 2:
        log("ciclo {n}: 2 posiciones, sin campo. Skip.".format(n=n)); return
    if avail < 1.3:
        log(f"ciclo {n}: disponible bajo. Skip."); return

    r = scan(top_n_volume=80, min_quote_vol=2_000_000, top_n=10, min_top_score=50, exclude_majors=True)
    btc = r.get("btc_4h", 0)
    log(f"ciclo {n}: BTC4h={btc}")
    cands = [c for c in r.get("top", []) if c["symbol"] not in held]
    if not cands:
        log(f"ciclo {n}: sin candidatas TOP nuevas."); return

    posts = fetch_square_posts(max_pages=25)
    now = datetime.now(timezone.utc); start = now - timedelta(hours=12)
    cnt = Counter()
    for p in posts:
        dt = p.get("datetime")
        if not dt or dt < start: continue
        for c in extract_coins_from_post(p.get("text", ""), p.get("hashtags", []), p.get("trading_pairs", [])):
            cnt[c] += 1

    for t in cands:
        sym, side = t["symbol"], t["side"]
        coin = sym.replace("USDT", "")
        menc = cnt.get(coin, 0)
        if menc > 1:
            log(f"descartada {sym}: {menc} menciones (ruido)"); continue
        if side == "SHORT" and btc > 0.5:
            log(f"descartada {sym}: SHORT con BTC +{btc}"); continue
        if side == "LONG" and btc < -0.5:
            log(f"descartada {sym}: LONG con BTC {btc}"); continue
        fl = flow_bias(sym) or {}
        fb = fl.get("bias", "unknown")
        if (fb == "bullish" and side == "SHORT") or (fb == "bearish" and side == "LONG"):
            log(f"descartada {sym}: flow {fb} contra {side}"); continue
        # === ABRIR ===
        px = float(requests.get(BASE + "/fapi/v1/ticker/price", params={"symbol": sym}, timeout=10).json()["price"])
        qty = rnd_step(11.0 / px, filters(sym)[1])
        if qty <= 0:
            log(f"descartada {sym}: qty 0"); continue
        try:
            signed("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": 10})
        except Exception as e:
            log(f"leverage {sym}: {e}")
        tick, step, minqty = filters(sym)
        side_o = "SELL" if side == "SHORT" else "BUY"
        pos_side = side  # hedge mode
        o = signed("POST", "/fapi/v1/order", {"symbol": sym, "side": side_o, "type": "MARKET",
                                              "quantity": qty, "positionSide": pos_side})
        if "orderId" not in o:
            log(f"ERROR abriendo {sym}: {o}"); return
        entry = px
        tp = entry * (0.95 if side == "SHORT" else 1.05)
        sl = entry * (1.07 if side == "SHORT" else 0.93)
        close_side = "BUY" if side == "SHORT" else "SELL"
        signed("POST", "/fapi/v1/order", {"symbol": sym, "side": close_side, "type": "TAKE_PROFIT_MARKET",
                                          "stopPrice": round(tp, 8), "closePosition": "true",
                                          "positionSide": pos_side})
        signed("POST", "/fapi/v1/order", {"symbol": sym, "side": close_side, "type": "STOP_MARKET",
                                          "stopPrice": round(sl, 8), "closePosition": "true",
                                          "positionSide": pos_side})
        log(f"ABIERTA {sym} {side} qty={qty} entry~{entry} TP={tp:.4g} SL={sl:.4g} menc={menc} flow={fb} BTC={btc}")
        return
    log(f"ciclo {n}: ninguna candidata pasó el filtro.")

if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    sync_clock()
    for i in range(1, N + 1):
        try:
            cycle(i)
        except Exception as e:
            log(f"ERROR ciclo {i}: {e}")
        if i < N:
            log(f"durmiendo 1h...")
            time.sleep(3600)
    log("loop terminado.")
