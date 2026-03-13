import os
import shutil
from pathlib import Path

def main():
    # Find the project root directory assuming the script is in scripts/
    script_dir = Path(__file__).parent.resolve()
    project_root = script_dir.parent

    print(f"Starting cleanup of project data in: {project_root}")

    # 1. Clean test caches (.pytest_cache and __pycache__)
    pytest_cache = project_root / '.pytest_cache'
    if pytest_cache.exists() and pytest_cache.is_dir():
        try:
            shutil.rmtree(pytest_cache)
            print("✅ Removed .pytest_cache")
        except Exception as e:
            print(f"❌ Error removing .pytest_cache: {e}")

    pycache_count = 0
    for p in project_root.rglob('__pycache__'):
        if p.is_dir():
            try:
                shutil.rmtree(p)
                pycache_count += 1
            except Exception:
                pass
    if pycache_count > 0:
        print(f"✅ Removed {pycache_count} __pycache__ directories")

    # 2. Clean data/ folder items
    data_dir = project_root / 'data'
    if not data_dir.exists():
        print(f"⚠️ Data directory not found: {data_dir}")
        return

    # Helper function to clear a directory's contents
    def clear_directory(target_dir):
        if target_dir.exists() and target_dir.is_dir():
            count = 0
            for item in target_dir.glob('*'):
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                    count += 1
                except Exception as e:
                    print(f"❌ Error removing {item.name}: {e}")
            if count > 0:
                print(f"✅ Cleaned {target_dir.name}/ ({count} items removed)")
            else:
                print(f"⚠️ {target_dir.name}/ is already empty")

    # 2a. Logs, News, Prices folders
    clear_directory(data_dir / 'logs')
    clear_directory(data_dir / 'news')
    clear_directory(data_dir / 'prices')

    # 2b. JSON files active_trades.json & trade_history.json
    for json_file in ['active_trades.json', 'trade_history.json']:
        file_path = data_dir / json_file
        if file_path.exists():
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write('[]\n')
                print(f"✅ Reset {json_file}")
            except Exception as e:
                print(f"❌ Error resetting {json_file}: {e}")

    # 2c. steps.log
    steps_log = data_dir / 'steps.log'
    if steps_log.exists():
        try:
            with open(steps_log, 'w', encoding='utf-8') as f:
                f.write('')
            print("✅ Cleared steps.log")
        except Exception as e:
            print(f"❌ Error clearing steps.log: {e}")

    # 2c. steps.log
    trades_log = data_dir / 'trades.log'
    if trades_log.exists():
        try:
            with open(trades_log, 'w', encoding='utf-8') as f:
                f.write('')
            print("✅ Cleared trades.log")
        except Exception as e:
            print(f"❌ Error clearing trades.log: {e}")

    print("\n🎉 Cleanup completed successfully!")

if __name__ == "__main__":
    main()
