#!/bin/bash
# Test script to verify strategy discovery logic

echo "=== Strategy Discovery Test ==="
echo ""

echo "1. Checking config/strategies/ directory:"
ls -la config/strategies/ 2>/dev/null || echo "Directory not found!"
echo ""

echo "2. Strategy JSON files found:"
for f in config/strategies/*.json; do
    if [ -f "$f" ]; then
        name=$(basename "$f" .json | tr '[:lower:]' '[:upper:]')
        echo "  - $f -> $name"
    fi
done
echo ""

echo "3. Checking active.json:"
if [ -f config/active.json ]; then
    echo "  EXISTS - current strategy:"
    grep -o '"strategy"[^,]*' config/active.json
else
    echo "  NOT FOUND"
fi
echo ""

echo "4. Checking STYLE_PRESETS in bot_config.json:"
grep -o '"MACDX"\s*:' bot_config.json | head -2
echo ""

echo "5. Checking AVAILABLE_STRATEGIES constant:"
grep "AVAILABLE_STRATEGIES" src/telegram_panel/backend/routes/config_routes.py
