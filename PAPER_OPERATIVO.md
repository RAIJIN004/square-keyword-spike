# PAPER OPERATIVO — Método Rumor Burst + Momentum
*Validado en real 29-sep-2026. Cuenta pequeña 3-4 USDT, futuros Binance, apalancamiento 10x.*

## TESIS (no olvidar)
1. **Los traders de Square opinan DESPUÉS del movimiento (lagging).** El cashtag `$COIN` se llena cuando ya pasó.
2. **El rumor se difunde ANTES (leading).** Keywords (`listing`, `buyback`, `banking`, `partnership`, `unlock`) + `$COIN` con pico irregular = entrada anticipada.
3. **Olvido + técnico bajista = SHORT limpio.** Foco creciente + rumor favorable + técnico alcista = LONG.
4. **Saturación = salida.** Cuando Square se llena de posts del movimiento ("+13%", "TAKE PROFIT") = cerrar aunque no toque TP.
5. **LONGS > SHORTS.** Validado: MON SHORT `-0.81`, ETHFI LONG `+1.07`. En mercado alcista (BTC 4h +) solo longs; en BTC 4h − solo shorts o fuera.

## FLUJO SIEMPRE
1. `scan_unified(top_n_volume=80, min_quote_vol=3M, top_n=12, min_top_score=50)` → solo TOP con book alineado. ALT con book contrario = descartar.
2. `square_hashtag(coin, windowMinutes=120-180)` → ¿hay pico de opiniones? ¿a favor o en contra? Verificar que el ticker sea EL CRIPTO (GLW stock ≠ GLW crypto — falso positivo real ocurrido).
3. `detect_keyword_spike` → z≥2 = burst. keywords genéricas pueden dar 0 aunque el rumor real sea específico (`buyback + banking` en ETHFI).
4. Orderbook + klines 15m confirman.
5. Entrada 10x, notional ~14-19 USDT con 3 USDT (margen ~1.4-1.9, dejar resto libre). Hedge mode REAL: `positionSide=LONG/SHORT` obligatorio (error -4061 si falta). Multi-Assets: NO intentar ISOLATED (error -4168). Respetar tick size (error -4014).
6. TP/SL con `closePosition=true` INMEDIATO tras llenar.
7. Vigilar saturación Square para salida anticipada.

## ERRORES YA PAGADOS
- MON SHORT -0.81: mercado giró BTC -0.58 → +1.21, short contra-tendencia. Lección: no shorts con BTC 4h positivo.
- GLW: posts de Square eran de la ACCIÓN Corning, no del cripto. Lección: validar `tradingPairsV2.code` / contexto.
- BE: scanner LONG pero Square pedía SHORT 20x. Lección: si rumor va en contra, descartar.

## RESULTADOS
- SEI SHORT testnet +9.4% ROE. MON SHORT real -0.81. ETHFI LONG real +1.076 (TP 0.7784). Neto positivo.
