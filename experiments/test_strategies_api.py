#!/usr/bin/env python3
"""Test the strategies endpoint to verify MACDX is included."""

import sys
sys.path.insert(0, '/tmp/gh-issue-solver-1773336278226')

import os
from pathlib import Path

# Set up environment to simulate container environment
os.environ['PANEL_CONFIG_PATH'] = '/tmp/gh-issue-solver-1773336278226/bot_config.json'

from src.telegram_panel.backend.routes.config_routes import (
    _use_new_config_system,
    CONFIG_DIR,
    STRATEGIES_DIR,
    AVAILABLE_STRATEGIES,
)

print("=== Testing Strategies API Logic ===")
print()

# Check paths
print(f"CONFIG_DIR: {CONFIG_DIR}")
print(f"CONFIG_DIR exists: {CONFIG_DIR.exists()}")
print(f"STRATEGIES_DIR: {STRATEGIES_DIR}")
print(f"STRATEGIES_DIR exists: {STRATEGIES_DIR.exists()}")

# Check active.json
active_path = CONFIG_DIR / "active.json"
print(f"active.json path: {active_path}")
print(f"active.json exists: {active_path.exists()}")

# Check new config system
use_new = _use_new_config_system()
print(f"_use_new_config_system(): {use_new}")
print()

# List strategy files
print("=== Strategy Files ===")
if STRATEGIES_DIR.exists():
    for path in STRATEGIES_DIR.glob("*.json"):
        print(f"  - {path.name} -> {path.stem.upper()}")
else:
    print("  STRATEGIES_DIR does not exist!")

print()
print(f"AVAILABLE_STRATEGIES constant: {AVAILABLE_STRATEGIES}")

# Simulate the list_strategies logic
print()
print("=== Simulated list_strategies Response ===")

import json

strategies = {}
if use_new and STRATEGIES_DIR.exists():
    json_files = list(STRATEGIES_DIR.glob("*.json"))
    print(f"Found {len(json_files)} json files")
    for path in json_files:
        name = path.stem.upper()
        try:
            with open(path) as f:
                config = json.load(f)
            strategies[name] = {
                "name": name,
                "description": config.get("_description", ""),
                "has_ai": name not in ["MACDX", "GRID"],
            }
        except Exception as e:
            print(f"Error loading {path}: {e}")

print(f"Strategies returned: {list(strategies.keys())}")
print(f"MACDX included: {'MACDX' in strategies}")
