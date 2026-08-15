# Trading Bot Test Website

A minimal Python website for testing a trading bot workflow with TradeLocker connectivity and FX Replay CSV-based replay evaluation.

## Features

- TradeLocker login connectivity check
- Bot test configuration form
- FX Replay CSV import and summary metrics
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

## Test

```bash
python -m pytest
```

## Notes

- TradeLocker is tested through its login API using credentials you provide in the UI.
- FX Replay does not currently expose a public API for direct third-party app control, so this starter site uses CSV exports from FX Replay instead.
