# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an automated trading system that integrates with Capital.com API for trading operations and DeepSeek API for AI-powered market analysis. The system operates in demo mode by default and supports multi-asset trading (forex, crypto, stocks, commodities). The system features a centralized dual-level logging architecture for comprehensive monitoring.

## Key Architecture

### Core Components

- **main.py**: Entry point, orchestrates the complete trading pipeline
- **logger.py**: Centralized logging system with dual-level logging (code.log for all events, trades.log for trading operations only)
- **collector.py**: Fetches price data and news from Capital.com API
- **analyzer.py**: Calculates technical indicators (SMA, RSI) and generates analysis prompts
- **predict.py**: Sends prompts to DeepSeek API for trading predictions
- **executor.py**: Opens/closes positions via Capital.com API with TP/SL management
- **monitor.py**: Tracks open positions and implements time-based risk management (auto-close at 60 min)
- **plotter.py**: Generates price charts with technical indicators
- **utils.py**: Shared utilities for Capital.com API (session management, request handling)
- **config.py**: Central configuration with API endpoints, trading parameters, and paths

### Data Flow Pipeline

1. **Data Collection** (collector.py): Price data from Capital.com → stored in `data/prices/`, news in `data/news/`
2. **Analysis** (analyzer.py): Technical indicators calculated (SMA, RSI) → prompts generated for AI
3. **AI Prediction** (predict.py): Analysis sent to DeepSeek API → trading signals with confidence scores
4. **Execution** (executor.py): Trading signals executed via Capital.com API with risk management
5. **Monitoring** (monitor.py): Open positions tracked → auto-closed at 60 minutes or on signal
6. **Visualization** (plotter.py): Charts generated → saved in `charts/`

### Configuration Management

- `MODE` controls demo vs real trading (default: "demo")
- Environment variables: `DEMO_USERNAME`, `DEMO_PASSWORD`, `DEEPSEEK_API_KEY`, `CAP_API_KEY`
- Trading parameters: position size (0.1 lots), take profit (1.5%), stop loss (2.0%)
- Supported symbols: EUR/USD, BTC/USD (default, can add AAPL, GOLD, OIL up to 5 max)
- Maximum concurrent positions: 5
- Position timeout: 60 minutes (auto-close)

## Development Commands

### Running the System

```bash
# Run complete trading pipeline
python3 main.py

# Run individual components for testing
python3 collector.py  # Test data collection
python3 analyzer.py   # Test analysis functions
python3 predict.py    # Test AI predictions
python3 executor.py   # Test position management
python3 monitor.py    # Test monitoring
python3 plotter.py    # Test chart generation
```

### Environment Setup

```bash
# Set required environment variables
export DEEPSEEK_API_KEY="your_deepseek_api_key"
export DEMO_USERNAME="your_demo_username"
export DEMO_PASSWORD="your_demo_password"
export CAP_API_KEY="your_capital_api_key"  # From Settings > API Integrations
export MODE="demo"  # or "real"
```

### Testing and Debugging

```bash
# Check syntax of all Python files
python3 -m py_compile *.py

# Test module imports
python3 -c "import logger, config, utils; print('OK')"

# Monitor logs in real-time
tail -f data/code.log      # All system events
tail -f data/trades.log    # Trading operations only

# Search for errors
grep ERROR data/code.log
```

### Log Files

- `data/code.log`: All system events (INFO, WARNING, ERROR, DEBUG)
  - Format: `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`
- `data/trades.log`: Only trading operations
  - Format: `%(asctime)s | %(message)s`

## File Structure

```
OpenProducer/
├── data/
│   ├── code.log              # All system events
│   ├── trades.log            # Trading operations only
│   ├── prices/               # OHLCV price data
│   │   ├── EUR_USD.json
│   │   └── BTC_USD.json
│   └── news/                 # News data
├── charts/                   # Generated price charts
├── *.py                      # Python modules (10 total)
└── *.md                      # Documentation files
```

## Key Features

- **Dual-level logging**: Separate logs for system events and trading operations
- **Session management**: Automatic token refresh and error recovery in utils.py
- **Risk management**: TP/SL on all positions, max 5 concurrent positions
- **Time-based exits**: Auto-close positions after 60 minutes
- **Error recovery**: Automatic session reinitialization on 401 errors
- **Safe logging**: All logging operations wrapped in safe_log() to prevent crashes

## API Integration Details

### Capital.com API
- Demo Base URL: `https://demo-api-capital.backend-capital.com/api/v1/`
- Real Base URL: `https://api-capital.backend-capital.com/api/v1/`
- Authentication: CST + Security Token (cached for 10 minutes)
- Endpoints: /session, /prices/{epic}, /positions, /positions/otc
- Epic mapping: EUR/USD → EURUSD, BTC/USD → BTCUSD
- Demo mode is determined by the API endpoint URL (demo URL = demo account, real URL = real account)

### DeepSeek API
- Endpoint: `https://api.deepseek.com/v1/chat/completions`
- Model: deepseek-chat
- Temperature: 0.3 (for consistent predictions)

## Recent Improvements

The system has been enhanced with:
- Centralized logging architecture
- Bug fixes in main.py (proper validation) and monitor.py (optimized imports)
- Comprehensive documentation (README.md, CHECKLIST.md)
- Automated directory creation for logs and data
- Fixed Capital.com API endpoint (demo: demo-api-capital.backend-capital.com, real: api-capital.backend-capital.com)
- Updated session token cache TTL from 5 to 10 minutes (matching API requirements)

## Important Notes

- System requires valid Capital.com demo account credentials for testing
- DeepSeek API key must be configured for AI predictions
- All trading operations are logged in `data/trades.log`
- Risk management implemented with configurable profit/loss thresholds
- Charts are auto-generated and saved in `charts/` directory
- Session tokens are cached for 10 minutes to reduce API calls (matching API limit)
- Positions are automatically closed after 60 minutes to limit exposure
- Demo API endpoint: https://demo-api-capital.backend-capital.com/api/v1/
- Real API endpoint: https://api-capital.backend-capital.com/api/v1/

## Troubleshooting

Common issues:
1. **401 Unauthorized**: System auto-recovery reinitializes session
2. **Empty price data**: Check Capital.com API status and epic mappings
3. **DeepSeek errors**: Verify API key and model availability
4. **Import errors**: Ensure all dependencies installed (pandas, matplotlib, requests)
5. **DNS/connectivity errors**: Use correct endpoint - demo: https://demo-api-capital.backend-capital.com/api/v1/