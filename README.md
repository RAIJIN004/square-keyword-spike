# square-keyword-spike (Rumor Burst)

MCP para Binance Square que detecta **picos irregulares de keywords rumor correlacionadas a una moneda**. Leading indicator: entra antes que los traders que opinan con el cashtag despues del movimiento.

## Por que existe

- **Cashtag `$COIN` = lagging.** El trader opina DESPUES del pump/dump.
- **Keyword rumor (`listing`, `partnership`, `unlock`, `buyback`...) + `$COIN` = leading.** La difusion del rumor aparece ANTES del movimiento.
- Teoria validada 29-sep-2026: moneda olvidada (0 menciones) + book bearish = SHORT limpio (SEI, SAGA). Moneda con foco + rumor favorable = LONG (ETHFI 7 bull vs 3 bear).

## Tools

### `detect_keyword_spike(coin, keywords, spike_window_minutes=60, baseline_hours=24, z_threshold=2.0, max_pages=20)`
Mide co-ocurrencia keyword + coin en Square. Devuelve por keyword: `current_with_coin`, `global_mentions`, `correlation_pct`, `baseline_mean_per_hour`, `z_score`, `is_irregular_spike`.

### `scan_all_rumors(coins, keywords, spike_window_minutes=60)`
Un solo fetch, ranking de bursts keyword->coin sin saber la moneda antes.

## Instalacion

```bash
pip install -r requirements.txt
```

## Uso como MCP (opencode / Hermes)

```json
"square-keyword-spike": {
  "type": "local",
  "command": ["C:\\Python313\\python.exe", "C:\\Users\\jhonv\\Downloads\\square-keyword-spike\\server.py"],
  "timeout": 120,
  "enabled": true
}
```

Hermes (`config.yaml`):
```yaml
square-keyword-spike:
  command: C:\Python313\python.exe
  args: ["C:\\Users\\jhonv\\Downloads\\square-keyword-spike\\server.py"]
  connect_timeout: 120
  enabled: true
```

## Fuente de datos

Endpoints publicos de Binance Square (sin API key), documentados en `wxie0815-arch/binance-square-monitor`:
- `GET bapi/composite/v3/friendly/pgc/content/article/list?type=2` (parsear `data.vos`)
- `GET bapi/composite/v4/friendly/pgc/feed/news/list`

## Ejemplo

```python
detect_keyword_spike(coin="ASTER", keywords=["listing","partnership","unlock"], spike_window_minutes=60)
# spike: {"keyword":"listing","current_with_coin":4,"global_mentions":6,"correlation_pct":66.7,"z_score":9.5,"is_irregular_spike":true}
```

## Trading

Ver `PROMPT_HERMES.md` para el metodo completo (scan unified -> filtro rumor -> orderbook -> entrada 10x -> TP/SL -> salida en saturacion).
