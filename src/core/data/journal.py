"""Изолированный атомарный journal instance; интерфейс DecisionJournal сохранён."""


from src.core.tracking.journal import DecisionJournal
from src.exchanges.market_data_storage import AtomicJsonStore, instance_prices_path
from src.utils.logger import warning


class InstanceDecisionJournal(DecisionJournal):
    def __init__(self, data_dir, instance_id):
        # Валидируем ID тем же контрактом, что и strategy snapshots.
        root = instance_prices_path(data_dir, instance_id, "BTCUSDT").parent.parent
        self._store = AtomicJsonStore(root / "decision_journal.json")
        super().__init__()

    def _load(self):
        value = self._store.read(default={})
        if not isinstance(value, dict):
            raise ValueError("Некорректный instance journal")
        return value

    def _save(self, symbol):
        try:
            with self._store._lock(exclusive=True):
                value = self._store._read(default={})
                if not isinstance(value, dict):
                    raise ValueError("Некорректный instance journal")
                if symbol in self.data:
                    value[symbol] = self.data[symbol]
                self._store._write(value)
                self.data = value
        except (OSError, ValueError, TypeError) as exc:
            warning(f"⚠️ Не удалось сохранить instance journal: {exc}")


def create_decision_journal(config):
    instance = config.get("STRATEGY_INSTANCE_ID")
    if not instance:
        return DecisionJournal()
    from src.config import DATA_DIR
    import os
    return InstanceDecisionJournal(config.get("DATA_DIR", os.getenv("SERVO_DATA_DIR", DATA_DIR)), instance)
