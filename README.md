# Binance PnL Tracker — Telegram Mini App

Tracks realized PnL across Binance **spot** and **futures**, broken down by
asset, with a running history — viewed as a Telegram Mini App.

- **Backend:** FastAPI + SQLite (`backend/`)
- **Frontend:** Telegram Mini App, vanilla HTML/CSS/JS, served by the same
  FastAPI app (`frontend/`)
- **Bot:** tiny polling bot that opens the Mini App (`backend/telegram_bot.py`)

## How the numbers work

- **Spot:** every trade you've synced is matched FIFO — each sell is
  matched against your oldest unsold buys of that asset to realize a
  gain/loss. Fees are pulled out separately so you can see them.
- **Futures:** Binance's `/fapi/v1/income` endpoint already reports realized
  PnL, commissions, and funding fees per position close — this app just
  sums and buckets them.
- Nothing here is unrealized/mark-to-market PnL on open positions (that
  needs a live price feed) — this is realized, "money that actually moved."

## 1. Get a Binance API key

Binance → API Management → Create API → **enable "Enable Reading" only**.
Do **not** enable withdrawals or even trading. This app never places
orders, so it doesn't need those permissions.

## 2. Create a Telegram bot + Mini App

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → save the token.
2. `/mybots` → your bot → **Bot Settings → Menu Button → Configure Menu
   Button** → set it to your Render URL (step 4) once you have it, or use
   the inline button the bot sends on `/start` (already wired up).

## 3. Push to GitHub

```bash
cd binance-pnl-tracker
git init
git add .
git commit -m "Initial PnL tracker"
gh repo create binance-pnl-tracker --private --source=. --push
# or push manually to a repo you created on github.com
```

Open the folder in VSCode (`code .`) to edit from here.

## 4. Deploy on Render

1. [render.com](https://render.com) → **New → Blueprint** → connect the
   GitHub repo. Render reads `render.yaml` and creates two services:
   - `pnl-tracker-api` (web service, serves the API + Mini App)
   - `pnl-tracker-bot` (background worker, the Telegram bot)
2. For each service, fill in the env vars marked `sync: false` in
   `render.yaml`:
   - **pnl-tracker-api**: `BINANCE_API_KEY`, `BINANCE_API_SECRET`,
     `SPOT_SYMBOLS` (e.g. `BTCUSDT,ETHUSDT,SOLUSDT`), `TELEGRAM_BOT_TOKEN`
   - **pnl-tracker-bot**: `TELEGRAM_BOT_TOKEN`, `WEBAPP_URL` (the API
     service's `.onrender.com` URL from step 2)
3. Deploy. The free tier's disk keeps your SQLite DB across deploys (see
   the `disk:` block in `render.yaml`) — without it, data resets on every
   redeploy.

## 5. Open it

Message your bot → `/start` → tap "Open PnL Tracker" → tap **Sync latest
trades** the first time to pull your history.

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
uvicorn main:app --reload
```

Visit `http://localhost:8000` — the Mini App UI will load outside Telegram
too (Telegram-specific features like `initData` just won't populate).

## Extending it

- **Unrealized PnL on open positions:** fetch `get_futures_positions()`
  (already in `binance_client.py`) and live spot prices via
  `GET /api/v3/ticker/price`, then diff against `avg_cost` in
  `pnl.compute_spot_pnl`.
- **More quote assets:** add them to `symbol_base_asset()` in `pnl.py`.
- **CSV export:** add an endpoint that dumps `SpotTrade`/`FuturesIncome`
  rows — the DB already has everything.
- **Auto-sync:** add a scheduled job (Render Cron Job, or APScheduler in
  the FastAPI app) that calls the sync endpoints hourly instead of a
  manual button.

## Security notes

- The API key only needs read permission — never widen it.
- `main.py` validates Telegram's `initData` signature so only your bot's
  users can hit the API once `TELEGRAM_BOT_TOKEN` is set — CORS is left
  open (`*`) for local dev; tighten `allow_origins` once you have your
  Render URL.
- Everything (keys, DB) stays on your own Render instance — nothing is
  sent anywhere else.
