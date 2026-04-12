import json
import os
from typing import Any, Dict, List

from ..exchanges.bingx_client import BingXClient
from ..utils.logger import error, info, warning


class DataLoader:
    """Загружает и управляет историческими данными для бэктеста."""

    def __init__(self, symbol: str, timeframe: str = "15m", limit: int = 1300):
        self.symbol = symbol.replace("-", "")  # Для файла: BTCUSDT
        self.timeframe = timeframe
        self.limit = limit
        self.data_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data",
            "prices",
            f"{self.symbol}.json",
        )
        self.klines: List[Dict[str, Any]] = []

    def fetch_data_from_exchange(self) -> List[Dict[str, Any]]:
        """Получает данные свечей с биржи."""
        try:
            client = BingXClient()
            klines = client.get_klines(self.symbol, self.timeframe, self.limit)
            if klines:
                # Сохранить в файл
                os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
                with open(self.data_path, "w") as f:
                    json.dump(klines, f, indent=2)
                info(
                    f"✅ Данные для {self.symbol} получены и сохранены в {self.data_path}"
                )
                return klines
            else:
                warning(f"⚠️ Не удалось получить данные для {self.symbol}")
                print(f"⚠️ Не удалось получить данные с биржи для {self.symbol}.")
                print(
                    f"   Проверьте: 1) API ключи в .env  2) сетевое подключение  3) SELinux (pasta)"
                )
                print(f"   Или положите данные вручную в {self.data_path}")
                return []
        except Exception as e:
            error(f"❌ Ошибка при получении данных: {e}")
            print(f"❌ Ошибка при получении данных с биржи: {e}")
            print(
                f"   Если SELinux блокирует pasta, используйте: --security-opt label=disable"
            )
            return []

    def load_data(self, fetch_if_missing: bool = True) -> List[Dict[str, Any]]:
        """Загружает данные из файла или получает с биржи."""
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r") as f:
                    self.klines = json.load(f)
                print(
                    f"✅ Данные загружены из {self.data_path}: {len(self.klines)} свечей"
                )
            except Exception as e:
                error(f"❌ Ошибка загрузки данных: {e}")
                self.klines = []
        elif fetch_if_missing:
            self.klines = self.fetch_data_from_exchange()
        else:
            warning(f"⚠️ Файл {self.data_path} не найден, и fetch отключен")
            self.klines = []

        # Сортировка по времени
        self.klines.sort(key=lambda x: x["snapshotTimeUTC"])
        return self.klines

    def get_klines(self) -> List[Dict[str, Any]]:
        """Возвращает загруженные свечи."""
        return self.klines
