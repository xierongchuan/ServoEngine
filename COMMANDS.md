# 🚀 OpenProducer - Быстрые команды

## ⚡ Основные команды

### Запуск
```bash
python3 run.py                           # Полный цикл
python3 -m src.core.collector            # Только сбор данных
python3 -m src.core.analyzer             # Только анализ
python3 -m src.core.predict              # Только прогноз
python3 -m src.core.executor             # Только торговля
python3 -m src.core.monitor              # Только мониторинг
python3 -m src.core.plotter              # Только графики
python3 tests/test_bingx.py              # Тест BingX API
```

### Автозапуск
```bash
./setup_cron.sh                          # Настроить cron
crontab -l                               # Показать задачи
crontab -r                               # Удалить все задачи
```

### Мониторинг
```bash
./monitor_logs.sh                        # Интерактивный мониторинг
tail -f data/steps.log                    # Логи системы
tail -f data/trades.log                  # Логи сделок
```

### Тестирование
```bash
python3 -m py_compile *.py               # Проверка синтаксиса
python3 -c "import logger, config, utils" # Проверка импортов
```

## 📊 Логи

```bash
# Просмотр
tail -n 50 data/steps.log                 # Последние 50 строк
grep ERROR data/steps.log                 # Только ошибки
grep "📌\|✅" data/trades.log             # Торговые операции

# Статистика
wc -l data/steps.log                      # Количество событий
wc -l data/trades.log                    # Количество сделок
```

## ⚙️ Управление

```bash
# Переменные окружения
export BINGX_API_KEY="key"               # API ключ BingX
export BINGX_SECRET_KEY="secret"         # API секрет BingX
export ENABLE_NEWS="true"                # Включить/выключить новости

# Запуск с выбором биржи
export EXCHANGE=bingx && python3 run.py

# Тестирование интеграции
export EXCHANGE=bingx && python3 tests/test_integration.py

# Права доступа
chmod +x *.sh                            # Сделать скрипты исполняемыми
```

## 🐳 Docker (опционально)

```bash
docker build -t openproducer .           # Собрать образ
docker run -d --name bot openproducer    # Запустить контейнер
docker logs -f bot                       # Логи контейнера
docker restart bot                       # Перезапустить
docker stop bot                          # Остановить
```

## 📁 Файлы

```bash
# Документация
cat README_RU.md                         # Русский README
cat README_QUICKSTART.md                 # Быстрый старт
cat CLAUDE.md                            # Тех. документация

# Скрипты
cat scripts/run_trading_bot.sh           # Скрипт запуска
cat scripts/setup_cron.sh                # Скрипт настройки cron
cat scripts/monitor_logs.sh              # Скрипт мониторинга

# Диагностика
python3 tests/test_bingx.py              # Тест BingX
```

## 🔍 Troubleshooting

```bash
# Ошибки импорта
pip install pandas matplotlib requests   # Установить зависимости

# Проверка API
curl -I https://open-api-vst.bingx.com/openApi/swap/v2/server/time # Статус BingX API (Demo)
curl -I https://openrouter.ai/api/v1                               # Статус OpenRouter API

# Логи системы
dmesg | tail                            # Системные логи (Linux)
sudo journalctl -u cron                 # Логи cron (Linux)
```

## 📦 Структура проекта

```
OpenProducer/
├── src/                                # Исходный код
│   ├── core/                           # Ядро (analyzer, collector, etc.)
│   ├── exchanges/                      # Адаптеры бирж
│   ├── utils/                          # Утилиты
│   ├── config.py                       # Конфигурация
│   └── main.py                         # Основная логика
├── scripts/                            # Bash скрипты
├── tests/                              # Тесты
├── data/                               # Данные и логи
│   ├── steps.log                       # Логи системы
│   ├── trades.log                      # Логи сделок
│   ├── prices/                         # Данные о ценах
│   └── news/                           # Новости
├── charts/                             # Графики
├── run.py                              # Точка входа
└── *.md                                # Документация
```

## ⚠️ Важные замечания

- ✅ Всегда используйте `demo` режим!
- ✅ Никогда не коммитьте API ключи!
- ✅ Проверяйте логи регулярно!
- ✅ Настройте уведомления об ошибках!

---

**Больше информации:** README_RU.md
