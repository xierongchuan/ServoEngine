#!/usr/bin/env python3
"""
Test script to verify strategies discovery logic.
Simulates what config_routes.py does when listing strategies.
"""

import json
from pathlib import Path


def main():
    # Simulate paths as they would be in the container
    # In container: CONFIG_PATH = /app/bot_config.json
    # So CONFIG_DIR = /app/config and STRATEGIES_DIR = /app/config/strategies

    # But for local testing, use relative paths
    project_root = Path(__file__).parent.parent
    config_dir = project_root / "config"
    strategies_dir = config_dir / "strategies"
    bot_config_path = project_root / "bot_config.json"

    print(f"Project root: {project_root}")
    print(f"Config dir: {config_dir} (exists: {config_dir.is_dir()})")
    print(f"Strategies dir: {strategies_dir} (exists: {strategies_dir.exists()})")
    print(f"Bot config: {bot_config_path} (exists: {bot_config_path.exists()})")
    print()

    # Check for new config system
    active_json = config_dir / "active.json"
    use_new_system = config_dir.is_dir() and active_json.exists()
    print(f"active.json exists: {active_json.exists()}")
    print(f"Use new config system: {use_new_system}")
    print()

    strategies = {}

    if use_new_system and strategies_dir.exists():
        print("Using NEW config system (reading from config/strategies/)")
        json_files = list(strategies_dir.glob("*.json"))
        print(f"Found {len(json_files)} JSON files: {[f.name for f in json_files]}")

        for path in json_files:
            name = path.stem.upper()
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            strategies[name] = {
                "name": name,
                "description": config.get("_description", ""),
                "preset": config.get("preset", {}),
                "has_ai": name not in ["MACDX", "GRID"],
            }
            print(f"  - {path.name} -> {name}")
    else:
        print("Using LEGACY config system (reading from bot_config.json)")
        with open(bot_config_path, "r", encoding="utf-8") as f:
            legacy = json.load(f)
        presets = legacy.get("STYLE_PRESETS", {})
        print(f"Found {len(presets)} presets: {list(presets.keys())}")

        for name, preset in presets.items():
            strategies[name] = {
                "name": name,
                "description": preset.get("description", ""),
                "preset": preset,
                "has_ai": name not in ["MACDX", "GRID"],
            }
            print(f"  - {name}")

    print()
    print("=" * 50)
    print("API Response:")
    print("=" * 50)
    response = {"strategies": strategies, "available": list(strategies.keys())}
    print(f"available: {response['available']}")
    print(f"Total strategies: {len(response['available'])}")

    # Check for MACDX
    if "MACDX" in response["available"]:
        print("\n✅ MACDX is present in the strategies list!")
    else:
        print("\n❌ MACDX is MISSING from the strategies list!")


if __name__ == "__main__":
    main()
