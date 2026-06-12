# Binance Futures Trading Bot

A Python CLI for placing and managing orders on the **Binance USDT-M Futures Testnet**.  
Clean architecture — HTTP client → order logic → CLI — with full logging, validation, and security.

---

## Supported Order Types

| Type | Category | Description |
|---|---|---|
| **MARKET** | Native | Instant fill at best price |
| **LIMIT** | Native | Resting order at specific price |
| **STOP_MARKET** | Native | Market order triggered at stop price *(mainnet only)* |
| **STOP_LIMIT** | Native | Limit order activated at stop price *(mainnet only)* |
| **OCO** | Strategy | Take-profit + stop-loss placed together *(mainnet only)* |
| **TWAP** | Algorithmic | Large order split into timed slices |
| **GRID** | Algorithmic | Ladder of buy/sell limits across a price range |

> **Testnet Note:** `STOP_MARKET`, `STOP_LIMIT`, and `OCO` require Binance's Algo Order endpoints which are not available on the Futures Testnet. These work correctly on mainnet. The bot detects this automatically and shows a clear message instead of a raw API error.

---

## Project Structure

```
trading_bot/
├── bot/
│   ├── client.py          # HMAC-signed REST client (no SDK, retry + rate limit handling)
│   ├── orders.py          # All 7 order types + result dataclasses
│   ├── validators.py      # Input validation for every parameter
│   └── logging_config.py  # Dual file+console logger
├── cli.py                 # Typer CLI — all commands
├── logs/                  # Auto-created log files
├── .env.example           # Credential template (safe to commit)
├── .env                   # Your actual keys — never committed
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Get Testnet API Keys

1. Go to [https://testnet.binancefuture.com](https://testnet.binancefuture.com)
2. Log in with GitHub
3. Navigate to **API Key** → Generate → copy Key and Secret

### 2. Install Dependencies

```bash
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Set Credentials

```bash
# Windows
copy .env.example .env
notepad .env

# macOS/Linux
cp .env.example .env
nano .env
```

Fill in your keys in `.env`:
```
BINANCE_API_KEY=your_testnet_key_here
BINANCE_API_SECRET=your_testnet_secret_here
```

> Keys are loaded via `python-dotenv` — never hardcoded, never logged, never committed.

---

## Usage

### Test connection
```bash
python cli.py ping
```

### Place Orders

#### MARKET order
```bash
python cli.py place-order --symbol BTCUSDT --side BUY --type MARKET --qty 0.001
```

#### LIMIT order
```bash
python cli.py place-order --symbol BTCUSDT --side SELL --type LIMIT --qty 0.001 --price 95000
```

### TWAP execution
```bash
# Split 0.01 BTC into 3 slices, every 10 seconds
python cli.py twap --symbol BTCUSDT --side BUY --qty 0.01 --slices 3 --interval 10
```

### GRID trading
```bash
# Place 6 evenly-spaced orders between 65000 and 70000
python cli.py grid --symbol BTCUSDT --lower 65000 --upper 70000 --levels 6 --qty 0.001
```

### Position Management

#### View open positions and balances
```bash
python cli.py account-info
```

#### View open orders (with Order IDs)
```bash
python cli.py open-orders --symbol BTCUSDT
```

#### Close part of a position
```bash
python cli.py close-position --symbol BTCUSDT --qty 0.03
```

#### Close entire position
```bash
python cli.py close-position --symbol BTCUSDT --qty all
```

#### Cancel a specific order
```bash
python cli.py cancel-order --symbol BTCUSDT --order-id 15008115849
```

#### Cancel all open orders for a symbol
```bash
python cli.py cancel-all --symbol BTCUSDT
```

### Help
```bash
python cli.py --help
python cli.py place-order --help
python cli.py close-position --help
python cli.py twap --help
python cli.py grid --help
```

---

## Logging

Logs written to `logs/trading_bot_YYYYMMDD.log`.

```
2025-06-12T14:18:00 | INFO  | TWAP START | symbol=BTCUSDT | slices=3 | interval=10s
2025-06-12T14:18:11 | INFO  | ORDER SUCCESS | orderId=15008098971 | status=FILLED | avgPrice=63371.60
2025-06-12T14:18:21 | INFO  | TWAP COMPLETE | filled_qty=0.009 | 3/3 slices placed
2025-06-12T14:22:18 | INFO  | GRID COMPLETE | buy_orders=3 | sell_orders=3 | total=6
```

- **File**: DEBUG level — all requests, responses, order events
- **Console**: WARNING level — keeps CLI output clean
- API keys and signatures are **never** logged

---

## Validation & Error Handling

Every parameter is validated before any API call:

| Scenario | Message |
|---|---|
| Bad symbol | `Symbol 'XYZ' must end with BUSD or USDT` |
| Missing price | `--price is required for LIMIT orders` |
| OCO missing legs | `OCO requires both --tp-price and --sl-price` |
| TWAP < 2 slices | `--slices must be an integer >= 2` |
| Grid lower ≥ upper | `--lower must be less than --upper` |
| Close qty > position | `Insufficient position. Available: 0.06 BTC. Tip: use --qty all` |
| Order not found | `Order ID does not exist or is already filled/cancelled` |
| STOP/OCO on testnet | Clear explanation with mainnet tip (no raw API error shown) |
| Rate limit hit | `Rate limit hit. Please wait Xs before retrying` |
| Network timeout | `Request timed out after 10s. Please retry` |
| Missing .env | `Missing API credentials. Create a .env file...` |

---

## Security

- API credentials loaded from `.env` only — never CLI flags or hardcoded values
- `.env` is in `.gitignore` — will never be committed to Git
- HMAC-SHA256 signatures stripped from all logs
- Request timeout enforced (10s) on every API call
- Automatic retry on server errors (500/502/503/504) for GET requests only
- Rate limit (429) and IP ban (418) handled gracefully with clear messages

---

## Assumptions

1. **Testnet only** — base URL is `https://testnet.binancefuture.com`
2. **One-Way position mode** — Binance default; hedge mode would require `positionSide`
3. **OCO implementation** — Binance Futures does not natively link OCO legs on testnet; implemented as two separate orders (standard algo-desk pattern); works on mainnet
4. **TWAP / GRID are algorithmic** — not native Binance order types; implemented in the bot layer using series of MARKET/LIMIT orders
5. **Quantity precision** — if you get `-1111 (LOT_SIZE)`, round qty to the symbol's `stepSize` (e.g. BTC: 3 decimal places)
6. **Close position** — uses a MARKET order on the opposite side to flatten; quantity validated against live position before placing

---

## Tech Stack

| Library | Purpose |
|---|---|
| `requests` | HTTP REST calls with retry |
| `typer[all]` | CLI framework |
| `rich` | Tables, panels, progress bars |
| `python-dotenv` | `.env` credential loading |

No Binance SDK — all HMAC signing done transparently in `bot/client.py`.
