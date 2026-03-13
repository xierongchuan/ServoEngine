# AGENTS.md (Ask Mode)

This file provides guidance for understanding this codebase.

**⚠️ CRITICAL: All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host.**

## Non-Obvious Documentation Context

- **Primary documentation** - See CLAUDE.md in project root for comprehensive technical documentation (17K chars)
- **Code language** - Comments and logs in Russian; code identifiers in English
- **Telegram Panel independence** - Runs in separate container, does NOT affect trading bot functionality
- **Data files** - Located in `data/` directory: prices, news, logs, trades, calibration suggestions
- **Strategy modes** - Multiple strategies: SCALP (1m), AISCALP (5m), SWING (1h), GRID (1m), HYBRID (deterministic+AI), HYBRID_VETO (AI veto only)
- **Architecture** - Multiprocessing with supervisor-worker pattern; each symbol runs in separate process
