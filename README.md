# seed_g0uv4_trades

TradingView Pine Seeds feed for IBKR trade pills.

Source journal: Google Sheet `IBKR Trade Journal`.

Encoding (one row per day, up to 3 trades in T1/T2/T3):
- open: 1 buy, 2 sell
- high: quantity
- low: price
- close: realized pnl + 100000
- volume: HHMM in UTC
