# PAPER — Sistema RumorBurst / Autotrader (estado actual)

Fecha: 2026-10-05. Bot: `autotrader.py` (LIVE con `--live`) + `server.py` (detección) + `hourly_loop.py` (firma Binance) + `trend-finder-unified/unified.py` (klines). Ciclo fijo 30s.

## 1. Tesis

Todas las monedas siguen a BTC. Se opera el **fade del gauge de comunidad**: SHORT en
`BULL fuerte-bajando` (sobreextendido cayendo), LONG en `BEAR fuerte-subiendo` (rebote).
Moneda default: **HYPE** (`DEFAULT_COIN`), que se arma sin su señal. Sin BTC en entradas.

## 2. Datos de sentimiento (Binance Square, sin API key)

`fetch_square_posts(max_pages=25)`: 3 endpoints públicos, ~630-680 posts por ciclo.
Normalización a `{id, text, hashtags, trading_pairs, timestamp, ts_ms, datetime UTC}`.

## 3. Detección (`top_rumor_discovery`, `server.py`)

Ventanas: spike 120m vs baseline 12h + early 30m. z-poisson ≥2 = spike.
`rank_score = z·(1+min(|chg24h|,20)/20) + share + rumor`. Directivas `ENTER_EARLY_*`
(burst + net ±2 + precio no volado ±5%), `WATCH_*`, `AVOID`, `WAIT`. Candidatas ordenadas
por `|chg_24h|` desc (movers primero).

## 4. Brújula gauge (manda en entradas y salidas)

`net_total` bull-bear 60m: `≥+4` BULL fuerte, `+1..3` leve, `0` plana, `-1..-3` bear leve,
`≤-4` BEAR fuerte; tendencia 2ª vs 1ª mitad. Plano con sesgo: `PLANA-alcista/bajista`.

## 5. Entradas (fade fuerte + momentum direccional, sin rangos)

LONG solo `BEAR fuerte-subiendo`, SHORT solo `BULL fuerte-bajando` (comunidad y HYPE auto).
Momentum como confirmación direccional (cualquier magnitud, sin deadband/rangos).
Hedge permitido (LONG+SHORT simultáneos); scale-in si ROE ≤ −10% (máx 2 agregados).
Sizing mínimo del exchange con qty redondeada (blindaje `-1111`: 2dp si aplica),
apalancamiento siempre al máximo, sin bloqueos de stop/balance.

## 6. Salidas

- Breakeven lock: a ROE +5% la salida ya no baja de fees (~notional×0.001).
- Escalera TP bot-side [+50,+150,+300,+500]% ROE: 50% por MARKET por peldaño (los TPs
  de exchange no pueden partir por el mínimo $50).
- Sin SL. Sin salidas por multitud/sentimiento (retiradas).

## 7. Finanzas/contabilidad

PnL/mark reales vía merge `account` + `positionRisk`. `day_pnl` suma cierres MARKET
(prorratea parciales; los fills TP del exchange no los ve). `DAILY_STOP` solo avisa.

## 8. Backtests (`backtest_hype.py`, `backtest_sentiment.py`, 31d HYPE)

- Solo-momentum: fade+reversa gana en fuerte (|mom|≥0.2: 58%), follow pierde siempre.
- Con rumor real (13.800 textos Reddit): FOLLOW fuerte 8h 61% (+0.449% med); fade pierde.
  Conclusión pendiente de aplicar: seguir al gauge fuerte, no fadearlo.

## 9. Archivos

`autotrader_state.json` (`day, day_pnl, open_ctx{entry,tp_tier,side,t0,adds}, mom_trail{peak,trough,locked},
btc_mom_prev, gauge_dir_prev, mom_chg_prev`), `hourly_log.txt`, `square_baseline.json`.
