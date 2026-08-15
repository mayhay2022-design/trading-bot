# Trading Bot Runner

A minimal Python website that creates a TradeLocker session, lets you choose an account, and runs a moving-average bot in paper or live mode.

## Features

- TradeLocker session and account loading
- Bot start, stop, and status flow
- Stop-loss and take-profit controls for bot runs
- Position and P&L status display
- Moving-average trading logic using TradeLocker market history
- FX Replay CSV import and summary metrics
- Persistent local storage for TradeLocker sessions and bot runs
- Lightweight Flask UI
- Basic automated endpoint tests

## Project Structure

```
trading-bot/
├── app.py
├── requirements.txt
├── static/
│   └── styles.css
├── templates/
│   └── index.html
└── tests/
    └── test_app.py
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the website

```bash
python app.py
```

Then open `http://127.0.0.1:5000`.

## TradeLocker setup

- Enter your TradeLocker username, password, server, and environment in the UI
- The app stores tokens and run state in a local `data/state.json` file for persistence across restarts
- If your broker requires it, set `TRADELOCKER_DEVELOPER_API_KEY` before starting the app

## Bot workflow

1. Create a TradeLocker session
2. Pick one of the returned accounts
3. Choose symbol, quantity, moving-average settings, and execution mode
4. Start the bot and monitor the latest signal from the status panel

Use **paper** mode to test safely. **Live** mode attempts to place real market orders through the connected TradeLocker account.

## Test

```bash
python -m unittest discover -s tests
```

## Notes

- The built-in strategy is a simple moving-average comparison using TradeLocker history data.
- FX Replay does not currently expose a public API for direct third-party app control, so this app uses CSV exports from FX Replay instead.
