"""Backtest con RUMOR REAL: sentimiento histórico Reddit (Arctic Shift, r/CryptoCurrency,
posts+comentarios 31d) con las wordlists del bot vs precio HYPE posterior.
FADE = opuesto al neto comunidad, FOLLOW = a favor. Salidas: horizontes + reversa.
Fees 0.10%. Solo lectura."""
import requests, json, time, statistics
from datetime import datetime, timezone, timedelta

BULL = ["longsetup","long","bullish","bull","breakout","breaks resistance","accumulat","reclaim","upside","demand zone","bounce","rebound","buying","buyers","higher","target"]
BEAR = ["shortsetup","short","bearish","bear","breakdown","breaks support","distribut","rejection","reject","dump","sell","selling","sellers","lower","support break","resistance reject"]
SUB = "CryptoCurrency"
END = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
START = END - timedelta(days=31)
F = "https://fapi.binance.com"

def fetch(ep, after, before, pages, extra=None):
    out, cur = [], int(after.timestamp())
    for _ in range(pages):
        p = {"subreddit": SUB, "after": cur, "before": int(before.timestamp()),
             "limit": 100, "sort": "asc"}
        if extra: p.update(extra)
        try:
            d = requests.get(f"https://arctic-shift.photon-reddit.com/api/{ep}/search",
                             params=p, timeout=30).json().get("data", [])
        except Exception as e:
            print("fetch err", e); break
        if not d: break
        out += d
        cur = d[-1]["created_utc"] + 1
        if len(d) < 100: break
        time.sleep(0.4)
    return out

print("bajando posts+comentarios 31d...", flush=True)
texts = []  # (epoch, texto)
day = START
pgs = cgs = 0
while day < END:
    nxt = day + timedelta(days=1)
    for x in fetch("posts", day, nxt, 2):
        texts.append((x["created_utc"], f"{x.get('title','')} {x.get('selftext','')}"))
        pgs += 1
    for x in fetch("comments", day, nxt, 5):
        b = x.get("body", "")
        if b and b != "[removed]":
            texts.append((x["created_utc"], b))
            cgs += 1
    day = nxt
print(f"posts+comments: {pgs} posts, {cgs} comentarios")
json.dump([{"t": t, "x": x[:500]} for t, x in texts],
          open(r"C:\Users\jhonv\AppData\Local\Temp\opencode\sent31.json", "w"))

# serie horaria neta
hours = {}
for t, x in texts:
    h = int(t // 3600) * 3600
    tl = x.lower()
    b = sum(1 for w in BULL if w in tl)
    r = sum(1 for w in BEAR if w in tl)
    if b or r:
        o = hours.setdefault(h, [0, 0])
        o[0] += b; o[1] += r
net = {h: v[0] - v[1] for h, v in hours.items()}
print(f"horas con señal: {len(net)}")

# klines HYPE 1h
kl, end = [], None
while len(kl) < 800:
    p = {"symbol": "HYPEUSDT", "interval": "1h", "limit": 800 - len(kl)}
    if end: p["endTime"] = end
    d = requests.get(F + "/fapi/v1/klines", params=p, timeout=20).json()
    if not d: break
    kl = d + kl; end = d[0][0] - 1
px = {int(k[0] // 3600000) * 3600: float(k[4]) for k in kl}

FEES = 0.10
def stats(rs, tag):
    if not rs:
        print(tag, "sin trades"); return
    w = [r for r in rs if r > 0]
    print(f"{tag}: n={len(rs)} win={len(w)/len(rs)*100:.0f}% prom={statistics.mean(rs):+.3f}% med={statistics.median(rs):+.3f}%")

for thr in (2, 3, 5):
    ev = sorted((h, (1 if v > 0 else -1)) for h, v in net.items() if abs(v) >= thr and h in px)
    print(f"\n-- |net|>={thr}: {len(ev)} eventos --")
    for mode in ("fade", "follow"):
        for H in (4, 8):
            rs = []
            for (h, d) in ev:
                if h + H * 3600 not in px: continue
                rs.append((px[h + H * 3600] / px[h] - 1) * 100 * (-d if mode == "fade" else d) - FEES)
            stats(rs, f"{mode} {H}h")
    # reversa: cierra cuando el neto horario voltea de signo
    for mode in ("fade", "follow"):
        rs = []
        hs = sorted(net.keys())
        for (h, d) in ev:
            side = -d if mode == "fade" else d
            closed = None
            for hh in hs:
                if hh <= h or hh not in px or hh > h + 24 * 3600: continue
                nd = 1 if net[hh] > 0 else (-1 if net[hh] < 0 else 0)
                if nd != 0 and nd != d:
                    closed = (px[hh] / px[h] - 1) * 100 * side - FEES
                    break
            if closed is None and h + 24 * 3600 in px:
                closed = (px[h + 24 * 3600] / px[h] - 1) * 100 * side - FEES
            if closed is not None: rs.append(closed)
        stats(rs, f"{mode}+reversa")
