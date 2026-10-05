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
BTC_MOM_DEADBAND_PCT = 0.0  # sin deadband a pedido: cualquier valor >0 SUBIENDO, <0 BAJANDO
MOM_MICRO_PCT = 0.020  # zona micro: dentro, las entradas/salidas van por VALOR del momentum
MOM_TRAIL_PCT = 0.020  # trailing: retroceso del momentum que cierra la posición
MOM_FILTER_ON = False  # momento-precio no filtra (manda el gauge fade); se deja el MOM en el log
ADD_LOSS_ROE_PCT = 10.0  # agregar a abierta solo si va perdiendo >= X% ROE
MAX_ADDS = 2  # máximo de agregados por posición (lado)
TP_LADDER_ROE = [50.0, 150.0, 300.0, 500.0]  # escalera: un TP activo; al llenarse, el siguiente más lejos
SOL_AUTO = True  # moneda automática: sin su señal, solo con la dirección de BTC
DEFAULT_COIN = "HYPE"  # moneda default para el modo auto (antes SOL)
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

def _step_dec(v):
    try:
        s = ("%f" % float(v)).rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0
    except Exception:
        return 8

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

def cancel_side_orders(sym, side):
    # Cancela solo las órdenes (TP/SL) del lado cerrado, sin tocar el hedge contrario.
    try:
        oo = signed("GET", "/fapi/v1/openOrders", {"symbol": sym})
        for o in (oo or []):
            if isinstance(o, dict) and o.get("positionSide") == side and o.get("orderId"):
                try:
                    signed("DELETE", "/fapi/v1/order", {"symbol": sym, "orderId": o["orderId"]})
                except Exception as e2:
                    log(f"cancel orden {o.get('orderId')} {sym}/{side}: {e2}")
    except Exception as e:
        log(f"listar openOrders {sym}: {e}")

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
    _mom, _mom_chg, _mom_ok = "PLANA", 0.0, False
    try:
        _mc = [float(k[4]) for k in _btc_klines("BTCUSDT", "15m", 6)]
        if len(_mc) >= 5 and _mc[-5]:
            _mom_chg = round((_mc[-1] / _mc[-5] - 1) * 100, 3)
            _mom = "SUBIENDO" if _mom_chg > BTC_MOM_DEADBAND_PCT else ("BAJANDO" if _mom_chg < -BTC_MOM_DEADBAND_PCT else "PLANA")
            _mom_ok = True
        log(f"BTC MOM 60m: {_mom_chg:+.3f}% -> {_mom}")
    except Exception as e:
        log(f"BTC MOM no disponible ({e}), se asume PLANA.")
    # Reversas (lo único que habilita entradas y arma la moneda auto): pasar A subir arma LONGs,
    # pasar A bajar arma SHORTs, venga de donde venga (incluye salir del plano).
    # La continuación se ignora y en PLANA no se entra. La salida es la inversa.
    _mom_pchg = st.get("mom_chg_prev", _mom_chg)
    st["mom_chg_prev"] = _mom_chg
    # Dirección del valor en micro (a pedido): SHORT aunque mom>0 si viene bajando
    # (previamente al alza); LONG aunque mom<0 si viene subiendo. Igual => quieto.
    if abs(_mom_chg) <= MOM_MICRO_PCT and _mom_ok:
        if _mom_chg > _mom_pchg:
            _micro_side = "LONG"
        elif _mom_chg < _mom_pchg:
            _micro_side = "SHORT"
        else:
            _micro_side = None
    else:
        _micro_side = None
    if _micro_side:
        log(f"MICRO BTC: {_mom_pchg:+.3f} -> {_mom_chg:+.3f} (arma {_micro_side}s)")
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

    # Brújula: el GAUGE manda (más confiable que el momento de precio a pedido).
    # BULL = {BULL fuerte, bull leve}, BEAR = {BEAR fuerte, bear leve}, resto = PLANA.
    _gdir = "PLANA"
    _gtrend = "estable"
    _glean = ""
    _glabel = ""
    try:
        _gl = (_gg.get("gauge") or "")
        _glabel = _gl
        _gtrend = (_gg.get("trend") or "estable")
        _gdir = "BULL" if _gl in ("BULL fuerte", "bull leve") else ("BEAR" if _gl in ("BEAR fuerte", "bear leve") else "PLANA")
        # Plano con sesgo: la tendencia (2ª mitad vs 1ª) dice hacia dónde mira el plano.
        if _gdir == "PLANA":
            _glean = "-alcista" if _gtrend == "subiendo" else ("-bajista" if _gtrend == "bajando" else "")
        log(f"brújula gauge: {_gdir}{_glean} ({_gl} {_gtrend}, n={_gg.get('mentions')})")
    except Exception as e:
        log(f"brújula gauge no disponible ({e}), se asume PLANA.")

    _gdir_prev = st.get("gauge_dir_prev", _gdir)
    if _gdir != _gdir_prev:
        log(f"REVERSA gauge: {_gdir_prev} -> {_gdir}")
    st["gauge_dir_prev"] = _gdir

    # 1) GESTIONAR ABIERTAS. BREAKEVEN LOCK (trailing por retroceso desactivado).
    #    Al tocar ROE +5% la salida ya no baja de +0.005. Clave por lado (hedge).
    tr = st.setdefault("mom_trail", {})
    qc = st.setdefault("quiet_cycles", {})
    just_closed = set()
    for p in pos:
        sym = p["symbol"]; coin = sym.replace("USDT", "")
        _tk = f"{sym}:{p.get('positionSide', 'BOTH')}"
        is_long = float(p["positionAmt"]) > 0
        pnl = _pnl_of(p)
        reason = None
        if _mom_ok:
            t0 = tr.get(_tk)
            if not t0:
                t0 = {"peak": _mom_chg, "trough": _mom_chg, "locked": False}
                tr[_tk] = t0
            _init_m = float(p.get("positionInitialMargin", 0)) or 1
            _roe = pnl / _init_m * 100
            # Breakeven NETO (fees medidos ~0.025 por pata): el lock no baja del costo.
            _fees = abs(float(p.get("notional", 0))) * 0.001
            if not t0.get("locked") and _roe >= 5.0:
                t0["locked"] = True
                log(f"breakeven lock {sym} (ROE +{_roe:.1f}%): la salida ya no baja de +{_fees:.3f} (fees).")
            if t0.get("locked") and pnl <= _fees:
                reason = (f"breakeven lock (pnl={pnl:.3f} <= fees~{_fees:.3f})")
            # Trailing por retroceso DESACTIVADO a pedido (se piensa algo mejor).
            # Se conserva el breakeven lock y el registro de pico/suelo.
            if is_long:
                if _mom_chg > t0["peak"]:
                    t0["peak"] = _mom_chg
            else:
                if _mom_chg < t0["trough"]:
                    t0["trough"] = _mom_chg
        # Sesgo plano drenando (a pedido): con posición abierta, el plano con sesgo
        # en contra sale para no dejar que el retroceso lento se coma el profit.
        # LONG sale en plana-bajando, SHORT en plana-alcista.
        if reason is None:
            if is_long and _gdir == "PLANA" and _gtrend == "bajando":
                reason = (f"plano-bajista drenando LONG (pnl={pnl:.3f})")
            elif (not is_long) and _gdir == "PLANA" and _gtrend == "subiendo":
                reason = (f"plano-alcista drenando SHORT (pnl={pnl:.3f})")
        # Escalera TP bot-side (a pedido): al tocar cada peldaño ROE se cierra 50%
        # por MARKET (reduceOnly, exento de mínimo). Un peldaño por ciclo.
        # Sin órdenes TP en el exchange (el mínimo $50 impediría partir).
        if reason is None and not DRY_RUN:
            _ck = st.setdefault("open_ctx", {}).get(_tk, {})
            _tier = _ck.get("tp_tier", 0)
            _i2 = float(p.get("positionInitialMargin", 0)) or 1
            _r2 = pnl / _i2 * 100
            while _tier < len(TP_LADDER_ROE) and _r2 >= TP_LADDER_ROE[_tier]:
                _amt = abs(float(p.get("positionAmt", 0)))
                try:
                    _, _st2, _, _ = filters(sym)
                    _d2 = _step_dec(_st2) if _st2 else 8
                except Exception:
                    _d2 = 8
                _qc = round(_amt / 2, _d2)
                if _qc <= 0 or _amt - _qc <= 0:
                    reason = (f"TP escalera final +{TP_LADDER_ROE[_tier]:.0f}% ROE (pnl={pnl:.3f})")
                    break
                _qcs = _qc if float(p.get("positionAmt", 0)) > 0 else -_qc
                _rc = close_market(sym, _qcs, p.get("positionSide", "BOTH"))
                if isinstance(_rc, dict) and "orderId" in _rc:
                    _frac = _qc / _amt if _amt else 1.0
                    st["day_pnl"] = float(st.get("day_pnl", 0)) + float(pnl) * _frac
                    _tier += 1
                    _ck["tp_tier"] = _tier
                    log(f"TP PARCIAL {sym} peldaño {_tier}/{len(TP_LADDER_ROE)} (+{TP_LADDER_ROE[_tier-1]:.0f}% ROE) x{_qc} orderId={_rc['orderId']}")
                    beep("exit_win")
                    break
                log(f"ERROR TP parcial {sym}: {_rc}")
                break
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
                        cancel_side_orders(sym, ps)
                        st["day_pnl"] = float(st.get("day_pnl", 0)) + float(pnl)
                        st.get("open_ctx", {}).pop(f"{sym}:{ps}", None)
                        st.get("mom_trail", {}).pop(f"{sym}:{ps}", None)
                        qc.pop(coin, None)
                        st.setdefault("closed_syms", {})[sym] = time.time()
                        just_closed.add(f"{sym}:{ps}")
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
            _psk = f"{sym}:{p.get('positionSide', 'BOTH')}"
            if _psk in just_closed:
                log(f"ESTADO {sym} {p.get('positionSide')} recién CERRADA este ciclo, omitiendo.")
                continue
            entry_c = float(p.get("entryPrice", 0)); mark_c = _mark_of(p, entry_c)
            pnl_c = _pnl_of(p)
            init_c = float(p.get("positionInitialMargin", 0)) or 1
            t = by_coin.get(coin, {})
            ctx = st.get("open_ctx", {}).get(_psk, {})
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
    held = {(p["symbol"], p.get("positionSide", "BOTH")): p for p in pos}
    held_syms = set(held.keys())
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
    # (|chg_24h| desc) + moneda automática en el fade: bear->subiendo LONG,
    # bull->bajando SHORT, sin señal de la moneda auto (solo dirección de BTC).
    _cands = [t for t in res.get("top", [])
              if t.get("directive") in ("ENTER_EARLY_LONG", "ENTER_EARLY_SHORT")]
    _cands.sort(key=lambda t: abs(t.get("chg_24h") or 0), reverse=True)
    if SOL_AUTO and not any(t.get("coin") == DEFAULT_COIN for t in _cands):
        _sol_chg = by_coin.get(DEFAULT_COIN, {}).get("chg_24h")
        # Moneda auto solo en fuerte + fade (igual que el resto).
        if _glabel == "BULL fuerte" and _gtrend == "bajando":
            _cands.insert(0, {"coin": DEFAULT_COIN, "directive": "ENTER_EARLY_SHORT",
                              "directive_reason": f"{DEFAULT_COIN} auto: BULL fuerte-bajando",
                              "chg_24h": _sol_chg})
        elif _glabel == "BEAR fuerte" and _gtrend == "subiendo":
            _cands.insert(0, {"coin": DEFAULT_COIN, "directive": "ENTER_EARLY_LONG",
                              "directive_reason": f"{DEFAULT_COIN} auto: BEAR fuerte-subiendo",
                              "chg_24h": _sol_chg})
    for t in _cands:
        d = t.get("directive", "WAIT")
        if d not in ("ENTER_EARLY_LONG", "ENTER_EARLY_SHORT"):
            continue
        coin = t["coin"]; sym = f"{coin}USDT"
        if coin == "BTC":
            log("BTC: no se abre (modo solo-altcoins)."); continue
        # Sin cooldown: si hay señal nueva, se entra (el usuario lo pidió).
        # confirmación técnica rápida (sin scan completo): flow + book alineados
        side = "LONG" if d == "ENTER_EARLY_LONG" else "SHORT"
        _key = f"{sym}:{side}"
        if _key in held_syms:
            # Scale-in (a pedido): solo si va perdiendo >= umbral y quedan agregados.
            # Lado contrario libre: hedge permitido (la cuenta está en hedge).
            _hp = held.get(_key, {})
            _hinit = float(_hp.get("positionInitialMargin", 0)) or 1
            _hroe = _pnl_of(_hp) / _hinit * 100 if _hp else 0.0
            _hctx = st.get("open_ctx", {}).get(_key, {})
            if _hroe > -ADD_LOSS_ROE_PCT or _hctx.get("adds", 0) >= MAX_ADDS:
                log(f"{coin} {side}: ya abierta (ROE {_hroe:.1f}%, adds={_hctx.get('adds', 0)}), no se agrega."); continue
            log(f"{coin} {side}: AGREGA #{_hctx.get('adds', 0)+1} en pérdida (ROE {_hroe:.1f}%).")
        # Entradas SOLO en fuerte + fade (a pedido, se retiran las anteriores):
        # SHORT solo BULL fuerte-bajando, LONG solo BEAR fuerte-subiendo.
        if side == "LONG" and not (_glabel == "BEAR fuerte" and _gtrend == "subiendo"):
            log(f"{coin}: LONG skipeado, no hay BEAR fuerte-subiendo ({_glabel} {_gtrend})."); continue
        if side == "SHORT" and not (_glabel == "BULL fuerte" and _gtrend == "bajando"):
            log(f"{coin}: SHORT skipeado, no hay BULL fuerte-bajando ({_glabel} {_gtrend})."); continue
        # Momentum a favor sin rango definido (a pedido): basta la dirección del
        # valor (subiendo=LONG, bajando=SHORT), sin importar la magnitud.
        if _mom_ok:
            if side == "LONG" and not (_mom_chg > _mom_pchg):
                log(f"{coin}: LONG skipeado, momentum no a favor ({_mom_pchg:+.3f}->{_mom_chg:+.3f})."); continue
            if side == "SHORT" and not (_mom_chg < _mom_pchg):
                log(f"{coin}: SHORT skipeado, momentum no a favor ({_mom_pchg:+.3f}->{_mom_chg:+.3f})."); continue
        # (El cálculo de _upturn/_downturn y el log GIRO son solo informativos.)
        # Dirección SOLO por comunidad (directiva). Flow/book retirados del path:
        # comprobado que definir dirección con datos es arriesgado y bloquea entradas buenas.
        px = float(requests.get(BASE + "/fapi/v1/ticker/price", params={"symbol": sym}, timeout=10).json()["price"])
        tick, step, minqty, minnot = filters(sym)
        import math as _m
        def _dec(v):
            try:
                s = ("%f" % float(v)).rstrip("0")
                return len(s.split(".")[1]) if "." in s else 0
            except Exception:
                return 8
        _sdec = _dec(step) if step else 8
        _tdec = _dec(tick) if tick else 8
        _target = (minnot or 5.0) if last_try else max(NOTIONAL, minnot or 5.0)
        raw = _target / px
        qty = round(_m.floor(raw / step) * step, _sdec) if step else raw
        if qty * px < (minnot or 5.0):
            qty = round(qty + (step or 0), _sdec)  # subir un step, alineado al step (evita -1111)
        # Blindaje -1111: probado en real y testnet que SOL rechaza 3dp (0.416/0.425)
        # aunque el step diga 0.001, y acepta 2dp (0.41/0.42). Si el mínimo cabe en
        # 2dp exactos sobre la grilla del step, se usan esos.
        try:
            if _sdec > 2 and step and minqty is not None:
                from decimal import Decimal as _D
                _st = _D(str(step)); _mq = _D(str(minqty)); _mn = _D(str(minnot or 5.0))
                _q2 = _D(str(int(float(qty) * 100))) / _D(100)
                for _ in range(4):
                    if _q2 * _D(str(px)) >= _mn:
                        break
                    _q2 += _D("0.01")
                if _q2 >= _mq and _q2 * _D(str(px)) >= _mn and (_q2 - _mq) % _st == 0 \
                        and _q2 * _D(str(px)) <= _D(str(float(qty) * px)) * _D("1.5"):
                    log(f"{coin}: qty 2dp anti-1111 ({qty} -> {_q2}).")
                    qty = float(_q2)
        except Exception as e:
            log(f"{coin}: aviso 2dp ({e}), usando qty step.")
        if qty <= 0 or qty < (minqty or 0) or qty * px < (minnot or 5.0):
            log(f"{coin}: sin tamaño para la cuenta (qty={qty} notional={qty*px:.2f} mín={minnot}). Skip al siguiente.")
            continue
        from hourly_loop import max_leverage
        maxlev = max_leverage(sym)
        need = qty * px
        # Apalancamiento SIEMPRE al máximo (a pedido): margen bloqueado mínimo,
        # queda resto libre para más trades. Margen = need/maxlev.
        lev = maxlev
        if need / lev > avail:
            log(f"{coin}: sin margen ni al máximo (necesita {need/lev:.2f}, hay {avail:.2f}). Skip.")
            continue
        log(f"{coin}: {lev}x margen~{need/lev:.2f} ({need/lev/max(avail,1e-9)*100:.0f}% del disp).")
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
            # Sin TP/SL en el exchange (a pedido): la escalera TP bot-side toma parciales
            # por MARKET al tocar cada peldaño; el mínimo $50 impediría partir en exchange.
            log(f"ABIERTA {sym} {side} qty={qty} @{px} escalera={TP_LADDER_ROE} (sin SL)")
            st["traded"][coin] = time.time()
            _oct = st.setdefault("open_ctx", {}).get(_key, {})
            _pa = abs(float(held.get(_key, {}).get("positionAmt", 0)))
            _pe = float(held.get(_key, {}).get("entryPrice", 0)) or px
            _tt = _pa + qty
            _avg = (_pa * _pe + qty * px) / _tt if _tt else px
            st.setdefault("open_ctx", {})[_key] = {"entry": _avg, "tp_tier": _oct.get("tp_tier", 0),
                                                  "side": side, "t0": _oct.get("t0", time.time()),
                                                  "adds": _oct.get("adds", 0) + (1 if _pa else 0)}
            st.setdefault("mom_trail", {})[_key] = {"peak": _mom_chg, "trough": _mom_chg, "locked": False}
            held_syms.add(_key)  # no duplicar dentro del mismo ciclo
        else:
            log("(dry-run, no se abre)")
        # Sin break: sigue con la siguiente señal del ciclo (hasta MAX_POS).
        # El margen se re-chequea por trade; refrescar disponible:
        try:
            avail = float(signed("GET", "/fapi/v2/account").get("availableBalance", avail))
        except Exception:
            pass
    return st

def cycle_sleep():
    # Ciclo fijo 30s (a pedido: operar el micro-momentum exige cercanía).
    return 30

if __name__ == "__main__":
    sync_clock()
    while True:
        try:
            save_state(run_once())
        except Exception as e:
            log(f"ERROR loop: {e}")
        _sl = cycle_sleep()
        log(f"--- esperando {_sl//60}m {_sl%60}s ---")
        time.sleep(_sl)
