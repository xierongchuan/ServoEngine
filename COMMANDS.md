# 🚀 OpenProducer - Быстрые команды

## ⚡ Основные команды

### Запуск
```bash
python3 main.py                          # Полный цикл
python3 collector.py                     # Только сбор данных
python3 analyzer.py                      # Только анализ
python3 predict.py                       # Только прогноз
python3 executor.py                      # Только торговля
python3 monitor.py                       # Только мониторинг
python3 plotter.py                       # Только графики
python3 test_bingx.py                    # Тест BingX API
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
export CAP_API_USERNAME="email"             # Настроить username
export CAP_API_PASSWORD="pass"              # Настроить password
export CAP_API_KEY="key"                 # API ключ Capital.com (ОБЯЗАТЕЛЬНО!)
export BINGX_API_KEY="key"               # API ключ BingX
export BINGX_SECRET_KEY="secret"    # Запуск с выбором биржи
export EXCHANGE=bingx && python3 main.py
export EXCHANGE=capital && python3 main.py

# Тестирование интеграции
export EXCHANGE=bingx && python3 test_integration.py
export EXCHANGE=capital && python3 test_integration.py
                # Выбор биржи (capital/bingx)
export DEEPSEEK_API_KEY="key"            # Настроить API ключ
echo $CAP_API_USERNAME                      # Проверить переменную

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
cat run_trading_bot.sh                   # Скрипт запуска
cat setup_cron.sh                        # Скрипт настройки cron
cat monitor_logs.sh                      # Скрипт мониторинга

# Диагностика
python3 test_api_structure.py           # Структура API запросов
```

## 🔍 Troubleshooting

```bash
# Ошибки импорта
pip install pandas matplotlib requests   # Установить зависимости

# Проверка API
curl -I https://demo-api-capital.backend-capital.com/api/v1/  # Статус Capital.com API (Demo)
curl -I https://api-capital.backend-capital.com/api/v1/        # Статус Capital.com API (Real)
curl -I https://open-api-vst.bingx.com/openApi/swap/v2/server/time # Статус BingX API (Demo)
curl -I https://api.deepseek.com                                # Статус DeepSeek API

# Логи системы
dmesg | tail                            # Системные логи (Linux)
sudo journalctl -u cron                 # Логи cron (Linux)
```

## 📦 Структура проекта

```
OpenProducer/
├── *.py                                # 10 Python модулей
├── *.sh                                # 3 Bash скрипта
├── *.md                                # 7 файлов документации
├── data/                               # Данные и логи
│   ├── steps.log                        # Логи системы
│   ├── trades.log                      # Логи сделок
│   ├── prices/                         # Данные о ценах
│   └── news/                           # Новости
└── charts/                             # Графики
```

## ⚠️ Важные замечания

- ✅ Всегда используйте `demo` режим!
- ✅ Никогда не коммитьте API ключи!
- ✅ Проверяйте логи регулярно!
- ✅ Настройте уведомления об ошибках!

---

**Больше информации:** README_RU.md
