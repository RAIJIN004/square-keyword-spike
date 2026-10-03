"""AUTOTRADER Rumor Burst: monitorea sentimiento (Square), entra en giros de BTC y sale solo.
Estrategia: el MOVIMIENTO de BTC manda (no las etiquetas bull/bear).
- Entradas: señal de comunidad + giro (bear->subiendo=LONG, bull->bajando=SHORT).
- Salidas (espejo): LONG cierra si BTC baja, SHORT si BTC sube. En plana: HOLD.
Sin bloqueos: ni stop diario ni balance mínimo frenan entradas (modo último intento).
LIVE = dinero real. DRY_RUN (sin --live) = solo loguea.
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
BTC_MOM_DEADBAND_PCT = 0.10  # |cambio 60m| menor => PLANA (sin movimiento, no se opera)
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

def _pnl_of(p):
    # /fapi/v2/account usa unrealizedProfit (minúscula), /fapi/v2/positionRisk usa unRealizedProfit
    return float(p.get("unRealizedProfit", p.get("unrealizedProfit", 0)))

def _mark_of(p, entry_fallback=0):
    m = p.get("markPrice", 0)
    try:
        m = float(m)
        if m > 0:
            return m
    except Exception:
        pass
    # fallback: precio ticker en vivo
    try:
        sym = p.get("symbol")
        px = float(requests.get(BASE + "/fapi/v1/ticker/price", params={"symbol": sym}, timeout=10).json()["price"])
        if px > 0:
            return px
    except Exception:
        pass
    return entry_fallback

def positions():
    acct = signed("GET", "/fapi/v2/account")
    if not isinstance(acct, dict) or "positions" not in acct:
        log(f"positions() respuesta inesperada: {str(acct)[:200]}")
        return 0.0, []
    avail = float(acct.get("availableBalance", 0))
    pos = [p for p in acct.get("positions", []) if abs(float(p.get("positionAmt", 0))) > 0]
    # Enriquecer con /fapi/v2/positionRisk que SÍ trae markPrice + unRealizedProfit frescos.
    # /fapi/v2/account no trae markPrice y usa unrealizedProfit (minúscula) -> por eso veías pnl=0.000.
    try:
        risk = signed("GET", "/fapi/v2/positionRisk")
        if not isinstance(risk, list):
            raise ValueError(f"positionRisk no lista: {str(risk)[:200]}")
        by_key = {(r.get("symbol"), r.get("positionSide")): r for r in (risk or [])
                  if isinstance(r, dict) and abs(float(r.get("positionAmt", 0))) > 0}
        for p in pos:
            r = by_key.get((p.get("symbol"), p.get("positionSide")))
            if r:
                if r.get("markPrice"):
                    p["markPrice"] = r.get("markPrice")
                if r.get("unRealizedProfit") is not None:
                    p["unRealizedProfit"] = r.get("unRealizedProfit")
                    p["unrealizedProfit"] = r.get("unRealizedProfit")
    except Exception as e:
        log(f"positionRisk merge falló ({e}), usando solo account.")
    return avail, pos

def close_market(sym, amt, pos_side):
    side = "SELL" if float(amt) > 0 else "BUY"
    qty = abs(float(amt))
    base = {"symbol": sym, "side": side, "type": "MARKET",
            "quantity": qty, "positionSide": pos_side}
    r = signed("POST", "/fapi/v1/order", {**base, "reduceOnly": "true"})
    # -1106: reduceOnly no requerido (ej. hedge/one-way) -> reintentar sin ese flag
    if isinstance(r, dict) and r.get("code") == -1106:
        log(f"close retry sin reduceOnly {sym}: {r}")
        r = signed("POST", "/fapi/v1/order", base)
    return r

def run_once():
    st = state()
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st = {"traded": {}, "day": today, "day_pnl": 0.0}
    daily_stop = st.get("day_pnl", 0) <= DAILY_STOP
    if daily_stop:
        log(f"stop diario alcanzado ({st.get('day_pnl', 0):.3f} <= {DAILY_STOP}): AVISO, entradas liberadas a pedido.")

    avail, pos = positions()
    log(f"balance disp={avail:.2f} abiertas={[p['symbol'] for p in pos]} modo={'LIVE' if not DRY_RUN else 'DRY'}")
    low_balance = avail < 1.20
    if low_balance:
        log(f"balance bajo ({avail:.2f} < 1.20): modo ÚLTIMO INTENTO (no bloquea).")

    res = top_rumor_discovery(spike_window_minutes=120, baseline_hours=12, max_pages=25,
                              top_n=12, min_total_mentions=3, with_price=True,
                              short_window_minutes=30, tradeable_only=True, exclude_noise=True)
    by_coin = {t["coin"]: t for t in res.get("top", [])}
    # (S/R retirado: bloqueaba giros válidos en lateral estrecho.)
    # Momento de precio BTC 60m (manda sobre etiquetas bull/bear):
    # SUBIENDO / BAJANDO = hay movimiento, PLANA = quieto (no se opera).
    _mom, _mom_chg = "PLANA", 0.0
    try:
        _mc = [float(k[4]) for k in _btc_klines("BTCUSDT", "15m", 6)]
        if len(_mc) >= 5 and _mc[-5]:
            _mom_chg = round((_mc[-1] / _mc[-5] - 1) * 100, 3)
            _mom = "SUBIENDO" if _mom_chg > BTC_MOM_DEADBAND_PCT else ("BAJANDO" if _mom_chg < -BTC_MOM_DEADBAND_PCT else "PLANA")
        log(f"BTC MOM 60m: {_mom_chg:+.3f}% -> {_mom}")
    except Exception as e:
        log(f"BTC MOM no disponible ({e}), se asume PLANA.")
    # Giros (lo único que habilita entradas): bear->subiendo arma LONGs, bull->bajando arma SHORTs.
    # La continuación ("entre bull" / "entre bear") se ignora: ahí ya se debió haber entrado.
    _mom_prev = st.get("btc_mom_prev", _mom)
    _upturn = (_mom_prev == "BAJANDO" and _mom == "SUBIENDO")
    _downturn = (_mom_prev == "SUBIENDO" and _mom == "BAJANDO")
    if _upturn or _downturn:
        log(f"GIRO BTC: {_mom_prev} -> {_mom} ({'arma LONGs' if _upturn else 'arma SHORTs'})")
    st["btc_mom_prev"] = _mom
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

    # Gauge BTC temprano (vale para veto de entradas Y salida cruda por volteo)
    from server import accel_gauge as _gauge
    try:
        _g = _gauge(coins=["BTC"], window_minutes=60, max_pages=8)
        _gg = (_g.get("gauges") or [{}])[0]
        _btc_net = _gg.get("net_total", 0)
        log(f"gauge BTC: {_gg.get('gauge')} {_gg.get('trend')} (net={_btc_net}, n={_gg.get('mentions')})")
    except Exception as e:
        log(f"gauge BTC falló ({e}), sin veto direccional.")
        _btc_net = 0

    # 1) GESTIONAR ABIERTAS. SALIDA ÚNICA (a pedido del usuario):
    #    Manda el MOVIMIENTO de BTC, no la etiqueta bull/bear:
    #    LONG se cierra si BTC BAJA (aunque siga "bull"), SHORT si BTC SUBE.
    #    En PLANA no hay movimiento => HOLD. Con profit o sin profit.
    qc = st.setdefault("quiet_cycles", {})
    just_closed = set()
    for p in pos:
        sym = p["symbol"]; coin = sym.replace("USDT", "")
        is_long = float(p["positionAmt"]) > 0
        pnl = _pnl_of(p)
        reason = None
        if is_long and _mom == "BAJANDO":
            reason = (f"BTC bajando ({_mom_chg:+.3f}%/60m, pnl={pnl:.3f})")
        elif (not is_long) and _mom == "SUBIENDO":
            reason = (f"BTC subiendo ({_mom_chg:+.3f}%/60m, pnl={pnl:.3f})")
        if reason:
            # CIERRE REAL: en LIVE se cierra por MARKET + se cancelan TP/SL restantes.
            pnl = _pnl_of(p)
            if DRY_RUN:
                log(f"*** SEÑAL SALIDA {sym} pnl={pnl:.3f} ({reason}) — dry-run, no se cierra ***")
            else:
                try:
                    amt = p.get("positionAmt")
                    ps = p.get("positionSide", "BOTH")
                    r = close_market(sym, amt, ps)
                    if isinstance(r, dict) and "orderId" in r:
                        log(f"CERRADA {sym} {ps} amt={amt} pnl~{pnl:.3f} ({reason}) orderId={r['orderId']}")
                        try:
                            signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": sym})
                        except Exception as e2:
                            log(f"cancel openOrders {sym}: {e2}")
                        st["day_pnl"] = float(st.get("day_pnl", 0)) + float(pnl)
                        st.get("open_ctx", {}).pop(sym, None)
                        qc.pop(coin, None)
                        st.setdefault("closed_syms", {})[sym] = time.time()
                        just_closed.add(sym)
                        beep("exit_win" if pnl >= 0 else "exit_loss")
                    else:
                        log(f"ERROR cierre {sym}: {r} — cerrar MANUAL")
                        beep("exit_loss")
                except Exception as e:
                    log(f"ERROR cierre {sym}: {e} — cerrar MANUAL")

    # ESTADO de abiertas (cada ciclo, para decidir salida a ojo):
    # mark, PnL/ROE, % al TP y al SL, sentimiento 30m, ciclos callados, edad
    for p in pos:
        try:
            sym = p["symbol"]; coin = sym.replace("USDT", "")
            if sym in just_closed:
                log(f"ESTADO {sym} recién CERRADA este ciclo, omitiendo.")
                continue
            entry_c = float(p.get("entryPrice", 0)); mark_c = _mark_of(p, entry_c)
            pnl_c = _pnl_of(p)
            init_c = float(p.get("positionInitialMargin", 0)) or 1
            t = by_coin.get(coin, {})
            ctx = st.get("open_ctx", {}).get(sym, {})
            age = f"{(time.time()-ctx['t0'])/60:.0f}m" if ctx.get("t0") else "manual"
            dist = ""
            if ctx.get("tp") and ctx.get("sl") and entry_c:
                tp_c, sl_c = float(ctx["tp"]), float(ctx["sl"])
                d_tp = (tp_c - mark_c) / mark_c * 100
                d_sl = (mark_c - sl_c) / mark_c * 100
                dist = f"alTP={d_tp:+.2f}% alSL={d_sl:+.2f}% | "
            log(f"ESTADO {sym} {p.get('positionSide')} entry={entry_c} mark={mark_c} "
                f"pnl={pnl_c:.3f} ROE={pnl_c/init_c*100:.1f}% edad={age} {dist}"
                f"early30m={t.get('early_mentions', t.get('early_mentions_proxy', '?'))} "
                f"net={t.get('net_early', '?')} callados={st.get('quiet_cycles', {}).get(coin, 0)} "
                f"crowd={t.get('is_crowd_arriving', False)} dir={t.get('directive', '?')}")
        except Exception as e:
            log(f"ESTADO error: {e}")

    # 2) ENTRAR: solo si hay campo Y balance suficiente
    _, pos = positions() if not DRY_RUN else (avail, pos)
    held_syms = {p["symbol"] for p in pos}
    # MODO ÚLTIMO INTENTO (a pedido): sin bloqueo por balance mínimo.
    # Con poco saldo se usa el nocional mínimo del exchange y el máximo apalancamiento.
    last_try = low_balance
    if last_try:
        log(f"balance mínimo ({avail:.2f}): ÚLTIMO INTENTO, nocional mínimo + apalancamiento máximo.")
    if len(pos) >= MAX_POS:
        log("sin campo."); return st
    # NOTA: sin veto direccional del gauge. El giro de precio manda: un SHORT en
    # bull-bajando (sobreextendido cayendo) es justo el mejor trade, y el veto lo
    # habría prohibido. El gauge queda solo informativo en el log.
    # Candidatas: señales de comunidad ordenadas por movimiento más pronunciado
    # (|chg_24h| desc) + SOL predeterminada: si hay giro BTC y SOL no trae señal,
    # se opera SOL igual con la dirección del giro (todas siguen a BTC).
    _cands = [t for t in res.get("top", [])
              if t.get("directive") in ("ENTER_EARLY_LONG", "ENTER_EARLY_SHORT")]
    _cands.sort(key=lambda t: abs(t.get("chg_24h") or 0), reverse=True)
    if not any(t.get("coin") == "SOL" for t in _cands):
        _sol_chg = by_coin.get("SOL", {}).get("chg_24h")
        if _upturn:
            _cands.insert(0, {"coin": "SOL", "directive": "ENTER_EARLY_LONG",
                              "directive_reason": "default SOL: giro BTC bear->subiendo, sin noticias",
                              "chg_24h": _sol_chg})
        elif _downturn:
            _cands.insert(0, {"coin": "SOL", "directive": "ENTER_EARLY_SHORT",
                              "directive_reason": "default SOL: giro BTC bull->bajando, sin noticias",
                              "chg_24h": _sol_chg})
    for t in _cands:
        d = t.get("directive", "WAIT")
        if d not in ("ENTER_EARLY_LONG", "ENTER_EARLY_SHORT"):
            continue
        coin = t["coin"]; sym = f"{coin}USDT"
        if sym in held_syms:
            log(f"{coin}: ya abierta, no duplicar."); continue
        # Sin cooldown: si hay señal nueva, se entra (el usuario lo pidió).
        # confirmación técnica rápida (sin scan completo): flow + book alineados
        side = "LONG" if d == "ENTER_EARLY_LONG" else "SHORT"
        # Filtro momento BTC: LONG solo SUBIENDO, SHORT solo BAJANDO, en PLANA no se entra
        if side == "LONG" and _mom != "SUBIENDO":
            log(f"{coin}: LONG skipeado, BTC {_mom} ({_mom_chg:+.3f}%/60m)."); continue
        if side == "SHORT" and _mom != "BAJANDO":
            log(f"{coin}: SHORT skipeado, BTC {_mom} ({_mom_chg:+.3f}%/60m)."); continue
        # Filtro giro: solo se entra en el giro (bear->subiendo LONG, bull->bajando SHORT).
        # La continuación se ignora: ahí ya se debió haber entrado.
        if side == "LONG" and not _upturn:
            log(f"{coin}: LONG skipeado, sin giro alcista (prev={_mom_prev})."); continue
        if side == "SHORT" and not _downturn:
            log(f"{coin}: SHORT skipeado, sin giro bajista (prev={_mom_prev})."); continue
        # Dirección SOLO por comunidad (directiva). Flow/book retirados del path:
        # comprobado que definir dirección con datos es arriesgado y bloquea entradas buenas.
        px = float(requests.get(BASE + "/fapi/v1/ticker/price", params={"symbol": sym}, timeout=10).json()["price"])
        _, step, minqty, minnot = filters(sym)
        import math as _m
        _target = (minnot or 5.0) if last_try else max(NOTIONAL, minnot or 5.0)
        raw = _target / px
        qty = _m.floor(raw / step) * step if step else raw
        if qty * px < (minnot or 5.0):
            qty = rnd_step(qty + (step or 0), step)  # subir un step, alineado al step (evita -1111)
        if qty <= 0 or qty < (minqty or 0) or qty * px < (minnot or 5.0):
            log(f"{coin}: sin tamaño para la cuenta (qty={qty} notional={qty*px:.2f} mín={minnot}). Skip al siguiente.")
            continue
        from hourly_loop import max_leverage
        maxlev = max_leverage(sym)
        need = qty * px
        import math as _m2
        if last_try:
            lev = maxlev  # todo lo que dé el exchange
            if need / lev > avail:
                log(f"{coin}: sin margen ni al máximo (necesita {need/lev:.2f}, hay {avail:.2f}). Skip.")
                continue
            log(f"{coin}: ÚLTIMO INTENTO {lev}x notional={need:.2f} con disp={avail:.2f}.")
        else:
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
            st.setdefault("open_ctx", {})[sym] = {"entry": px, "tp": tp, "sl": sl,
                                                  "side": side, "t0": time.time()}
            held_syms.add(sym)  # no duplicar dentro del mismo ciclo
        else:
            log("(dry-run, no se abre)")
        # Sin break: sigue con la siguiente señal del ciclo (hasta MAX_POS).
        # El margen se re-chequea por trade; refrescar disponible:
        try:
            avail = float(signed("GET", "/fapi/v2/account").get("availableBalance", avail))
        except Exception:
            pass
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
