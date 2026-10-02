"""AUTOTRADER Rumor Burst: monitorea, PITA en entrada, entra y sale solo.
Sin IA: dirección = voto bull/bear por keywords + confirmación técnica rápida.
Seguridad: 1 posición, notional ~10, cooldown 6h por moneda, stop diario -0.50 USDT,
solo opera si balance > 1.20. DRY_RUN=True = solo pita y loguea.
"""
import sys, json, time
import winsound
sys.path.insert(0, r"C:\Users\jhonv\Downloads\square-keyword-spike")
from server import top_rumor_discovery  # ANTES de añadir unified (ambos tienen server.py)
sys.path.insert(0, r"C:\Users\jhonv\Downloads\trend-finder-unified")

DRY_RUN = "--live" not in sys.argv
NOTIONAL = 10.0
MAX_POS = 3
COOLDOWN_H = 6
DAILY_STOP = -0.50
STATE = r"C:\Users\jhonv\Downloads\square-keyword-spike\autotrader_state.json"

from unified import klines as _btc_klines  # solo régimen BTC para el árbitro (no dirección)
from hourly_loop import signed, sync_clock, filters, rnd_step, BASE
import requests

def beep(kind):
    try:
        if kind == "entry":
            winsound.Beep(880, 300); winsound.Beep(1200, 400)
        elif kind == "exit_win":
            winsound.Beep(1200, 200); winsound.Beep(1200, 200); winsound.Beep(1500, 400)
        elif kind == "exit_loss":
            winsound.Beep(400, 400); winsound.Beep(300, 500)
    except Exception:
        pass

def log(m):
    print(m, flush=True)

def state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"traded": {}, "day": "", "day_pnl": 0.0}

def save_state(s):
    json.dump(s, open(STATE, "w"))

def positions():
    acct = signed("GET", "/fapi/v2/account")
    pos = [p for p in acct.get("positions", []) if abs(float(p.get("positionAmt", 0))) > 0]
    return float(acct.get("availableBalance", 0)), pos

def close_market(sym, amt, pos_side):
    side = "SELL" if float(amt) > 0 else "BUY"
    return signed("POST", "/fapi/v1/order", {"symbol": sym, "side": side, "type": "MARKET",
                                             "quantity": abs(float(amt)), "positionSide": pos_side,
                                             "reduceOnly": "true"})

def run_once():
    st = state()
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st = {"traded": {}, "day": today, "day_pnl": 0.0}
    if st["day_pnl"] <= DAILY_STOP:
        log("stop diario alcanzado. No opera."); return st

    avail, pos = positions()
    log(f"balance disp={avail:.2f} abiertas={[p['symbol'] for p in pos]} modo={'LIVE' if not DRY_RUN else 'DRY'}")
    low_balance = avail < 1.20
    if low_balance:
        log("balance mínimo: solo GESTIÓN, sin entradas nuevas.")

    res = top_rumor_discovery(spike_window_minutes=120, baseline_hours=12, max_pages=25,
                              top_n=12, min_total_mentions=3, with_price=True,
                              short_window_minutes=30, tradeable_only=True, exclude_noise=True)
    by_coin = {t["coin"]: t for t in res.get("top", [])}
    # Régimen BTC actual para el árbitro (¿volteó contra la posición?)
    try:
        from unified import klines as _kl
        _bc = [float(k[4]) for k in _btc_klines("BTCUSDT", "15m", 17)]
        btc_4h = round((_bc[-1] / _bc[0] - 1) * 100, 2) if _bc[0] else 0
    except Exception:
        btc_4h = 0.0
    # Para monedas abiertas sin burst visible en el top: veredicto rápido individual
    # (el top solo trae min_total_mentions>=3; una moneda enfriándose desaparece del top)
    from server import coin_signal as _cs
    for p in pos:
        _c = p["symbol"].replace("USDT", "")
        if _c not in by_coin:
            try:
                _s = _cs(coin=_c, window_minutes=180, max_pages=10)
                by_coin[_c] = {"coin": _c, "directive": "WAIT",
                               "net_early": _s.get("bull_hits", 0) - _s.get("bear_hits", 0),
                               "is_crowd_arriving": False,
                               "early_mentions_proxy": _s.get("early_mentions_30m", 0),
                               "early_z_proxy": _s.get("early_z", 0),
                               "mentions_proxy": _s.get("mentions", 0)}
            except Exception as e:
                log(f"coin_signal {_c}: {e}")
    from collections import Counter as _C
    log(f"top: {res.get('posts_fetched')} posts, " +
        str(dict(_C(t.get('directive', '?') for t in res.get('top', [])))) +
        " | SEÑALES: " + str([(t['coin'], t.get('directive')) for t in res.get('top', [])
                             if t.get('directive', 'WAIT') != 'WAIT']))

    # 1) GESTIONAR ABIERTAS: multitud, flip o RELEVANCIA PERDIDA = salir
    qc = st.setdefault("quiet_cycles", {})
    for p in pos:
        sym = p["symbol"]; coin = sym.replace("USDT", "")
        t = by_coin.get(coin, {})
        is_long = float(p["positionAmt"]) > 0
        net = t.get("net_early", 0)
        crowd = t.get("is_crowd_arriving", False)
        early_m = t.get("early_mentions_proxy",
                      t.get("early_mentions", t.get("early_mentions_30m", 1)))
        pnl = float(p.get("unRealizedProfit", 0))
        reason = None
        if crowd:
            reason = "multitud llegando (views)"
        elif is_long and net <= -2:
            reason = "sentimiento volteó a bear"
        elif not is_long and net >= 2:
            reason = "sentimiento volteó a bull"
        else:
            # RELEVANCIA PERDIDA: sin menciones tempranas 2 ciclos seguidos + en profit = cobrar
            if early_m == 0:
                qc[coin] = qc.get(coin, 0) + 1
            else:
                qc[coin] = 0
            if qc.get(coin, 0) >= 2 and pnl >= 0:
                reason = f"relevancia perdida ({qc[coin]} ciclos sin menciones, pnl={pnl:.3f})"
            # NIVEL 2 — ÁRBITRO IA: pérdida + posible volteo de régimen BTC o empate total
            init_m = float(p.get("positionInitialMargin", 1)) or 1
            roe = pnl / init_m * 100
            regime_against = (is_long and btc_4h < -0.5) or (not is_long and btc_4h > 0.5)
            if reason is None and roe < -10 and (regime_against or abs(net) <= 1):
                try:
                    from ai_arbiter import arbitrate
                    brief = {
                        "posicion": f"{sym} {p['positionSide']} entry={p.get('entryPrice')} mark={p.get('markPrice')}",
                        "pnl": f"{pnl:.4f} USDT ({roe:.1f}% ROE)",
                        "BTC_4h": f"{btc_4h}% (régimen {'EN CONTRA' if regime_against else 'neutral/a favor'})",
                        "sentimiento_coin_30m": f"net={net} (bull-bear)",
                        "menciones_tempranas": early_m,
                        "pregunta": ("¿El mercado cambió de dirección (régimen BTC) invalidando la tesis, "
                                     "o es ruido temporal para HOLD?")
                    }
                    arb = arbitrate(brief)
                    log(f"ÁRBITRO IA {sym}: {arb['decision']} ({arb['reason']}) [{arb.get('model')}]")
                    if arb["decision"] == "CLOSE":
                        reason = f"árbitro IA: {arb['reason']}"
                except Exception as e:
                    log(f"árbitro {sym}: {e}")
        if reason:
            pnl = float(p.get("unRealizedProfit", 0))
            log(f"SALIDA {sym} pnl={pnl:.3f} ({reason})")
            beep("exit_win" if pnl >= 0 else "exit_loss")
            if not DRY_RUN:
                close_market(sym, p["positionAmt"], p["positionSide"])
                st["day_pnl"] = round(st["day_pnl"] + pnl, 4)
            else:
                log("(dry-run, no se cierra)")

    # 2) ENTRAR: solo si hay campo Y balance suficiente
    _, pos = positions() if not DRY_RUN else (avail, pos)
    held_syms = {p["symbol"] for p in pos}
    if low_balance:
        log("sin entradas por balance mínimo."); return st
    if len(pos) >= MAX_POS:
        log("sin campo."); return st
    for t in res.get("top", []):
        d = t.get("directive", "WAIT")
        if d not in ("ENTER_EARLY_LONG", "ENTER_EARLY_SHORT"):
            continue
        coin = t["coin"]; sym = f"{coin}USDT"
        if sym in held_syms:
            log(f"{coin}: ya abierta, no duplicar."); continue
        last = st["traded"].get(coin, 0)
        if time.time() - last < COOLDOWN_H * 3600:
            log(f"{coin}: cooldown."); continue
        # confirmación técnica rápida (sin scan completo): flow + book alineados
        side = "LONG" if d == "ENTER_EARLY_LONG" else "SHORT"
        # Dirección SOLO por comunidad (directiva). Flow/book retirados del path:
        # comprobado que definir dirección con datos es arriesgado y bloquea entradas buenas.
        px = float(requests.get(BASE + "/fapi/v1/ticker/price", params={"symbol": sym}, timeout=10).json()["price"])
        _, step, minqty, minnot = filters(sym)
        import math as _m
        raw = max(NOTIONAL, minnot or 5.0) / px
        qty = _m.floor(raw / step) * step if step else raw
        if qty * px < (minnot or 5.0):
            qty = round(qty + (step or 0), 8)  # subir un step para cumplir mínimo
        if qty <= 0 or qty < (minqty or 0) or qty * px < (minnot or 5.0):
            log(f"{coin}: sin tamaño para la cuenta (qty={qty} notional={qty*px:.2f} mín={minnot}). Skip al siguiente.")
            continue
        from hourly_loop import max_leverage
        maxlev = max_leverage(sym)
        need = qty * px
        import math as _m2
        lev = _m2.ceil(need / max(avail, 0.01) * 1.5)  # margen con colchón 50%
        lev = max(1, min(lev, maxlev))
        if need / lev * 1.2 > avail:
            log(f"{coin}: sin margen con colchón (necesita {need/lev*1.2:.2f}, hay {avail:.2f}). Skip.")
            continue
        if lev > 10:
            log(f"{coin}: AVISO apalancamiento {lev}x (máx {maxlev}x) para que quepa el mínimo.")
        log(f"ENTRADA {sym} {side} qty={qty} @{px} ({t.get('directive_reason')})")
        beep("entry")
        if not DRY_RUN:
            try:
                signed("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": lev})
            except Exception as e:
                log(f"lev: {e}")
            so = "BUY" if side == "LONG" else "SELL"
            o = signed("POST", "/fapi/v1/order", {"symbol": sym, "side": so, "type": "MARKET",
                                                  "quantity": qty, "positionSide": side})
            if "orderId" not in o:
                log(f"ERROR entrada: {o}"); return st
            cs = "SELL" if side == "LONG" else "BUY"
            tp = px * (1.05 if side == "LONG" else 0.95)
            sl = px * (0.93 if side == "LONG" else 1.07)
            signed("POST", "/fapi/v1/order", {"symbol": sym, "side": cs, "type": "TAKE_PROFIT_MARKET",
                                              "stopPrice": round(tp, 8), "closePosition": "true", "positionSide": side})
            signed("POST", "/fapi/v1/order", {"symbol": sym, "side": cs, "type": "STOP_MARKET",
                                              "stopPrice": round(sl, 8), "closePosition": "true", "positionSide": side})
            log(f"ABIERTA {sym} TP={tp:.4g} SL={sl:.4g}")
            st["traded"][coin] = time.time()
        else:
            log("(dry-run, no se abre)")
        break
    return st

if __name__ == "__main__":
    sync_clock()
    while True:
        try:
            save_state(run_once())
        except Exception as e:
            log(f"ERROR loop: {e}")
        log("--- esperando 5 min ---")
        time.sleep(300)
