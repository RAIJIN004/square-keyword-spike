"""Backtest HYPE: reversión vs seguimiento de tendencia tras señales de momentum BTC.
Señal = MOM BTC 60m del bot (close[-1]/close[-5]-1). Sin sentimiento histórico
(el baseline está vacío): la dirección se toma del propio momentum BTC.
Modos: FADE (lado opuesto al movimiento BTC) vs FOLLOW (mismo lado).
Salidas: horizonte fijo (2h/4h/8h), reversa (flip de signo del MOM), trailing 0.020.
Fees asumidos 0.10% round-trip. Solo lectura, no opera."""
import requests, statistics

F = "https://fapi.binance.com"

def klines(sym, interval, n):
    out, end = [], None
    while len(out) < n:
        p = {"symbol": sym, "interval": interval, "limit": min(1000, n - len(out))}
        if end:
            p["endTime"] = end
        d = requests.get(F + "/fapi/v1/klines", params=p, timeout=20).json()
        if not d:
            break
        out = d + out
        end = d[0][0] - 1
        if len(d) < 1:
            break
    return out[-n:]

print("descargando klines...", flush=True)
btc = klines("BTCUSDT", "15m", 3000)
hype = klines("HYPEUSDT", "15m", 3000)
bc = [float(k[4]) for k in btc]
hc = [float(k[4]) for k in hype]
bt = [k[0] for k in btc]
assert len(bc) == len(hc), (len(bc), len(hc))
N = len(bc)
print(f"velas: {N} (~{N*15/60/24:.1f} días)")

def mom(i):
    return (bc[i] / bc[i - 4] - 1) * 100 if i >= 4 and bc[i - 4] else 0.0

FEES = 0.10  # % round trip

def simulate(side, t, exit_fn):
    """side +1 LONG HYPE, -1 SHORT. exit_fn(i) -> bool cerrar al cierre de i."""
    ep = hc[t]
    for i in range(t + 1, min(t + 96 + 1, N)):  # máx 24h
        r = (hc[i] / ep - 1) * 100 * side - FEES
        if exit_fn(i, r):
            return r, (i - t) * 15
    i = min(t + 96, N - 1)
    return (hc[i] / ep - 1) * 100 * side - FEES, (i - t) * 15

def stats(rs):
    if not rs:
        return "sin trades"
    w = [r for r in rs if r > 0]
    return (f"n={len(rs)} win={len(w)/len(rs)*100:.0f}% prom={statistics.mean(rs):+.3f}% "
            f"med={statistics.median(rs):+.3f}% mejor={max(rs):+.2f}% peor={min(rs):+.2f}%")

# Eventos: giro direccional del MOM (como el bot) con |mom|>=0.02 (micro o más)
events = []  # (t, dir_mom +1 subiendo / -1 bajando)
prev = 0
for t in range(5, N - 96):
    m = mom(t)
    d = 1 if m > 0 else (-1 if m < 0 else 0)
    if d != 0 and d != prev and abs(m) >= 0.005:
        events.append((t, d))
    if d != 0:
        prev = d
print(f"eventos de giro: {len(events)}")

def ex_horiz(h):
    n = int(h * 4)
    return lambda i, r, t0=None: (i - t0) >= n if t0 else False

print("\n== FADE (opuesto al movimiento BTC) ==")
for name, H in [("horiz 2h", 2), ("horiz 4h", 4), ("horiz 8h", 8)]:
    rs = []
    for (t, d) in events:
        n = int(H * 4)
        i = min(t + n, N - 1)
        rs.append((hc[i] / hc[t] - 1) * 100 * (-d) - FEES)
    print(f"fade {name}: {stats(rs)}")
print("== FOLLOW (a favor del movimiento BTC) ==")
for name, H in [("horiz 2h", 2), ("horiz 4h", 4), ("horiz 8h", 8)]:
    rs = []
    for (t, d) in events:
        n = int(H * 4)
        i = min(t + n, N - 1)
        rs.append((hc[i] / hc[t] - 1) * 100 * d - FEES)
    print(f"follow {name}: {stats(rs)}")

print("\n== con SALIDA POR REVERSA (cierra cuando el MOM voltea de signo) ==")
for mode in ("fade", "follow"):
    rs, holds = [], []
    for (t, d) in events:
        side = -d if mode == "fade" else d
        ep = hc[t]
        closed = None
        for i in range(t + 1, min(t + 96 + 1, N)):
            m = mom(i)
            if (m > 0 > mom(t)) or (m < 0 < mom(t)):
                pass
            md = 1 if m > 0 else (-1 if m < 0 else 0)
            if md != 0 and md != d:  # reversa del signo original
                closed = (hc[i] / ep - 1) * 100 * side - FEES
                holds.append((i - t) * 15)
                break
        if closed is None:
            i = min(t + 96, N - 1)
            closed = (hc[i] / ep - 1) * 100 * side - FEES
            holds.append((i - t) * 15)
        rs.append(closed)
    print(f"{mode} + reversa: {stats(rs)} hold_med={statistics.median(holds):.0f}min")

print("\n== con TRAILING 0.020 sobre el MOM (como el bot) ==")
for mode in ("fade", "follow"):
    rs = []
    for (t, d) in events:
        side = -d if mode == "fade" else d
        ep = hc[t]
        peak, trough = mom(t), mom(t)
        closed = None
        for i in range(t + 1, min(t + 96 + 1, N)):
            m = mom(i)
            peak, trough = max(peak, m), min(trough, m)
            hit = (m < peak - 0.020) if side == 1 else (m > trough + 0.020)
            if hit:
                closed = (hc[i] / ep - 1) * 100 * side - FEES
                break
        if closed is None:
            i = min(t + 96, N - 1)
            closed = (hc[i] / ep - 1) * 100 * side - FEES
        rs.append(closed)
    print(f"{mode} + trailing: {stats(rs)}")
