# PAPER OPERATIVO v2 — Método Rumor Burst + Momentum
*Cuenta real pequeña (3-4 USDT), futuros Binance, 10x. Validado 29-30 sep 2026.*

## REGLAS ESPECÍFICAS (obligatorias, en orden)

1. **DESCUBRIR (sin listas fijas):** `top_rumor_discovery` → top por `rank_score` (z-Poisson + share transversal + rumor-keyword + momentum). Solo tradeables con par USDT, sin stablecoins/acciones/fragmentos.
2. **SEÑAL TEMPRANA:** `early_burst` 30m (`early_mentions≥2`, `early_z≥2`) = gatillo precoz. Sin burst temprano, no hay entrada anticipada.
3. **VEREDICTO ÚNICO:** `coin_signal(coin)` → `ENTER_EARLY_LONG/SHORT | WAIT | AVOID_SATURATED`. Se obedece sin reinterpretar.
4. **ANTI-MULTITUD:** `check_saturation` → si `SATURATED` al entrar: DESCARTAR (techo de atención). Si se satura DESPUÉS en profit: SALIR sin esperar TP.
5. **RUMOR A FAVOR:** keywords rumor (`listing/buyback/banking/partnership/unlock`) presentes = leading. Sentimiento bull/bear Neto a favor del lado.
6. **TÉCNICO MÍNIMO:** unified TOP + book alineado. `eff<0.30` = no trade. BTC 4h a favor del lado (BTC+ → longs, BTC− → shorts).
7. **TICKER REAL:** validar que es el cripto (GLW acción ≠ cripto; LIT=Lighter ≠ Litentry). Revisar `tradingPairsV2`/contexto.
8. **EJECUCIÓN REAL (hedge):** `positionSide` obligatorio, 10x, notional ~10-19 USDT, TP/SL `closePosition=true` INMEDIATO. Errores: -4061 (falta positionSide), -4168 (no isolated en Multi-Assets), -4014 (tick size).
9. **PROHIBIDO:** promediar, entrar saturado, chasear >+5%, más de 2 posiciones, riesgo >40% cuenta/trade. Balance <1.20 → parar.
10. **VIGILANCIA:** cada 10-15 min Square de la posición. 5+ posts a favor = recta final → cerrar en market. Sentimiento volteado → cerrar ya.
11. **ACELERACIÓN MANDA:** entrar solo con atención ACELERANDO (early burst 30m, posts creciendo). Salir cuando se ENFRÍA. **Nunca re-entrar en enfriamiento** aunque el top la muestre (NIGHT 30 sep: salida +0.125 en enfriamiento, re-entrada inválida cerrada a breakeven). El top dice *dónde mirar*, la aceleración dice *cuándo*.

## LOG DE OPERACIONES (real, USDT)
| Fecha | Moneda | Lado | Entrada | Salida | PnL | Nota |
|---|---|---|---|---|---|---|
| 29 sep | MON | SHORT 700 | 0.02712 | 0.02828 | -0.81 | Giro BTC, SL |
| 29 sep | ETHFI | LONG 20 | 0.7246 | 0.7784 TP | +1.076 | Saturación post-entrada, salida perfecta |
| 29 sep | BE | LONG 0.04 | 296.45 | 288 SL | -0.338 | Entrada saturada (5-6 posts idénticos) |
| 29 sep | MVLL | LONG 0.35 | 35.38 | 34.70 SL | -0.238 | Saturada al entrar; filtro habría dado +0.25 saliendo en pico 36.2 |
| 30 sep | ADA | LONG 50 | 0.2485 | 0.2430 SL | -0.266 | Entrada saturada (13 menciones), nunca subió |
| 30 sep | XPL | SHORT 150 | 0.09119 | 0.09228 manual | -0.164 | Unlock 1.8B Sep 25 digiriéndose; taker bull en contra |
| 30 sep | LIT | SHORT 3 | 3.8391 | 3.8431 manual | -0.012 | Breakeven; LIT=Lighter (buybacks), no Litentry |
| 30 sep | BR | SHORT 16 | 0.6983 | 0.705 manual | -0.107 | Cerrado fuera de stops |
| 30 sep | NIGHT | LONG 250 | 0.03874 | 0.03924 manual | +0.125 | Solo-rumores: top menciones + opinión bull, salida en enfriamiento |
| testnet | SEI/BR | SHORT | — | — | SEI +1.2/BR/BR/SAGA en curso | Validación sin riesgo |

**Neto real aprox:** +1.076 +0.125 −0.81 −0.338 −0.238 −0.266 −0.164 −0.012 −0.107 ≈ **−0.73 USDT** (3.6→~1.8).
**Lección cara:** 4 de 6 pérdidas fueron entradas saturadas o contra-flujo. Con `coin_signal`+`check_saturation` esas 4 no se abrían.
