# PAPER — Sistema RumorBurst / Autotrader (estado actual)

Fecha: 2026-10-03. Bot: `autotrader.py` (LIVE con `--live`) + `server.py` (detección) + `hourly_loop.py` (firma Binance) + `trend-finder-unified/unified.py` (klines).

## 1. Tesis

Todas las monedas siguen a BTC. Se opera el **giro del movimiento de BTC** (no las etiquetas
bull/bear) sobre las monedas con movimientos más pronunciados, con **SOL como predeterminada**
aunque no traiga noticias: en giro se entra a SOL con la dirección del giro.

## 2. Datos de sentimiento (Binance Square, sin API key)

`fetch_square_posts(max_pages=25)`: 3 endpoints públicos (`article/list type=2/1`, `feed/news/list`),
~680 posts por ciclo. Cada post se normaliza a
`{id, text, hashtags, trading_pairs, timestamp, ts_ms, datetime UTC, viewCount, likeCount}`.
Timestamps ms→s se convierten a `datetime` UTC (`server.py:173-187`).

## 3. Detección (`top_rumor_discovery`, `server.py:543+`)

- Ventanas: spike 120m vs baseline 12h + early 30m. `now = datetime.now(timezone.utc)`.
- z_poisson = (recent − esperado) / sqrt(esperado+1); spike si z≥2, warming si 1≤z<2.
- Share transversal + bonus rumor (keywords) + momentum precio:
  `rank_score = z·(1+min(|chg24h|,20)/20) + share_bonus + rumor_bonus`.
- Con precio 24h Binance (`price, chg_24h, quote_vol_24h`); `tradeable_only` filtra sin par USDT.
- Directivas: `ENTER_EARLY_LONG/SHORT` (burst + net direccional ±2 + precio no volado ±5%),
  `WATCH_*`, `AVOID` (multitud o +8% corrido), `WAIT`.

## 4. Momento BTC (manda sobre todo)

- `BTC MOM 60m = close[-1]/close[-5] − 1` en velas 15m (~60-75m).
- Deadband `BTC_MOM_DEADBAND_PCT = 0.10`: `SUBIENDO > +0.10`, `BAJANDO < −0.10`, si no `PLANA`.
- Giros (único habilitador de entradas, `btc_mom_prev` persistido en el state):
  `BAJANDO→SUBIENDO` arma LONGs, `SUBIENDO→BAJANDO` arma SHORTs. Continuación y PLANA no entran.

## 5. Entradas (una por ciclo hasta MAX_POS=3)

Orden: candidatas `ENTER_*` ordenadas por `|chg_24h|` desc (movers primero) + SOL sintética
primera si hay giro (`default SOL: giro BTC…, sin noticias`). Filtros en orden:
`held` (no duplicar) → momento (LONG⊂SUBIENDO, SHORT⊂BAJANDO) → giro → tamaño mínimo
(`MIN_NOTIONAL`, qty alineada al step) → margen. Sin veto de gauge, sin cooldown,
sin bloqueos de stop diario ni balance mínimo.
- Normal: nocional `max(10, minnot)`, leverage `ceil(need/disp·1.5)` con colchón 20%.
- Último intento (`disp<1.20`): nocional = mínimo del exchange, leverage = máximo, sin colchones.
- Al abrir: leverage + MARKET + `TAKE_PROFIT_MARKET ±5%` + `STOP_MARKET ∓7%` (`closePosition=true`).
  OJO: con apalancamiento máximo la liquidación llega antes que el SL.

## 6. Salidas (espejo exacto de entradas)

LONG cierra si BTC marca BAJANDO; SHORT si marca SUBIENDO; en PLANA, HOLD. Con o sin profit.
Cierre MARKET + `reduceOnly` (si Binance devuelve `-1106`, reintento sin el flag) +
cancela órdenes abiertas + suma `day_pnl` + limpia `open_ctx`. Sin salidas por multitud,
sentimiento, gauge ni árbitro (retiradas a pedido).

## 7. Finanzas/contabilidad

- PnL/mark reales vía merge `account` + `positionRisk` (`markPrice` no existe en account y el
  campo PnL cambia de mayúsculas: `unrealizedProfit` vs `unRealizedProfit`).
- `day_pnl` realizado aprox. por suma de PnL no realizado al cerrar; se resetea por día.
- `DAILY_STOP=-0.50` y balance mínimo hoy solo avisan, no bloquean.

## 8. Archivos

`autotrader_state.json` (`day, day_pnl, open_ctx{tp,sl,side,t0}, quiet_cycles, closed_syms,
btc_mom_prev`), `hourly_log.txt`, `square_baseline.json`.
