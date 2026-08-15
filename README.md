# TradeLocker Trading Bot

A comprehensive Python-based trading bot for the TradeLocker platform with advanced strategies, backtesting, and risk management.

## Features

✅ **TradeLocker API Integration** - Full platform connectivity  
✅ **Technical Indicators** - MA, RSI, MACD, Bollinger Bands, Stochastic  
✅ **Backtesting Engine** - Extensive testing with performance metrics  
✅ **Multiple Strategies** - Trend-following, mean reversion, RSI+MACD combo  
✅ **Risk Management** - Stop-loss, take-profit, position sizing, drawdown protection  
✅ **Real-time Monitoring** - Live execution, alerts, P&L tracking  
✅ **85%+ Win Rate Target** - Iterative optimization and validation  

## Project Structure

```
trading-bot/
├── src/
│   ├── tradelocker_api.py      # TradeLocker API wrapper
│   ├── indicators.py            # Technical indicators library
│   ├── strategies.py            # Trading strategy implementations
│   ├── backtester.py            # Backtesting engine
│   ├── risk_manager.py          # Risk management module
│   ├── trading_engine.py        # Main execution engine
│   └── data_handler.py          # Historical data management
├── config/
│   ├── config.json              # Configuration template
│   └── strategies_config.json    # Strategy parameters
├── tests/
│   ├── test_indicators.py       # Indicator tests
│   ├── test_strategies.py       # Strategy tests
│   └── test_backtester.py       # Backtester tests
├── backtest_results/            # Backtest output reports
├── requirements.txt             # Python dependencies
├── setup.py                     # Package setup
└── main.py                      # Entry point
```

## Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure your TradeLocker API credentials in `config/config.json`
4. Run backtests or start live trading

## Usage

### Backtesting
```bash
python main.py --backtest --strategy rsi_macd --start-date 2024-01-01 --end-date 2024-12-31
```

### Live Trading
```bash
python main.py --live --strategy rsi_macd
```

## Strategies

- **RSI + MACD Combo** - Target: 85%+ win rate
- **Trend Following** - Moving average crossovers
- **Mean Reversion** - Bollinger Bands based
- **Volatility Based** - ATR and breakout strategy

## Performance Metrics

- Win Rate
- Profit Factor
- Sharpe Ratio
- Maximum Drawdown
- Return on Investment (ROI)

## Contributing

See CONTRIBUTING.md for guidelines.

## License

MIT License
