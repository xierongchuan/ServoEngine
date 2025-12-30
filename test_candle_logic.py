
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

# Mock config to test specific scenarios
import src.config as config

print("=== TEST START ===")
print("Testing Intraday configuration (Expected: 5m, ~288 candles)")

# SCENARIO 1: INTRADAY
config.STRATEGY_STYLE = "INTRADAY"
config.DEFAULT_CHART_RANGE = "1D"
config.CHART_RANGES = {
    "1D": {"days": 1, "candles": 1440, "interval": "1m"} # Old config style
}
config.STYLE_PRESETS = {
    "INTRADAY": {
        "timeframe": "5m",
        "chart_period": "1D",
    }
}

# Emulate parts of collector.py logic
def get_interval_limit():
    chart_config = config.CHART_RANGES.get(config.DEFAULT_CHART_RANGE, {})
    current_preset = config.STYLE_PRESETS.get(config.STRATEGY_STYLE)

    target_interval_str = current_preset.get("timeframe", "1m")

    chart_days = chart_config.get("days", 0)
    chart_hours = chart_config.get("hours", 0)
    chart_minutes = chart_config.get("minutes", 0)
    total_minutes = (chart_days * 1440) + (chart_hours * 60) + chart_minutes

    interval_min = config.parse_interval_minutes(target_interval_str)

    limit = int(total_minutes // interval_min)

    return target_interval_str, limit, total_minutes

res_int, res_limit, res_dur = get_interval_limit()
print(f"Result: TF={res_int}, Limit={res_limit}, Duration={res_dur}m")

if res_int == "5m" and res_limit == 288:
    print("✅ TEST PASSED: Intraday calculation correct")
else:
    print(f"❌ TEST FAILED: Expected 5m/288, got {res_int}/{res_limit}")

print("\nTesting Scalp configuration (Expected: 1m, ~360 candles)")

# SCENARIO 2: SCALP
config.STRATEGY_STYLE = "SCALP"
config.DEFAULT_CHART_RANGE = "6h"
config.CHART_RANGES = {
    "6h": {"hours": 6, "candles": 360, "interval": "1m"}
}
config.STYLE_PRESETS = {
    "SCALP": {
        "timeframe": "1m",
        "chart_period": "6h",
    }
}

res_int, res_limit, res_dur = get_interval_limit()
print(f"Result: TF={res_int}, Limit={res_limit}, Duration={res_dur}m")

if res_int == "1m" and res_limit == 360:
    print("✅ TEST PASSED: Scalp calculation correct")
else:
    print(f"❌ TEST FAILED: Expected 1m/360, got {res_int}/{res_limit}")
