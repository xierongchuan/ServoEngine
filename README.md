# 🤖 OpenProducer - Автоматизированная торговая система

## ⚠️ ВАЖНОЕ ПРЕДУПРЕЖДЕНИЕ

**ВНИМАНИЕ:** Торговля на финансовых рынках связана с высокими рисками и может привести к потере всех ваших средств.

Данная система предназначена **ИСКЛЮЧИТЕЛЬНО для образовательных целей**. Автоматическая торговля может привести к значительным убыткам.

**НИКОГДА НЕ ИСПОЛЬЗУЙТЕ СИСТЕМУ С РЕАЛЬНЫМИ ДЕНЬГАМИ БЕЗ ПОЛНОГО ПОНИМАНИЯ РИСКОВ!**

---

## 📋 Описание системы

**OpenProducer** - это полнофункциональная автоматизированная торговая система, которая интегрируется с **BingX API** (VST/Standard Futures) и Capital.com API для торговых операций и использует DeepSeek API для AI-анализа рынка. Система работает в демо-режиме по умолчанию и поддерживает торговлю на нескольких активах (forex, криптовалюты, акции, товары).

### Основные возможности

- 📊 Сбор рыночных данных с Capital.com API и BingX API
- 🧠 AI-анализ через DeepSeek API с **оптимизацией запросов** (Smart Skip)
- 📈 Технические индикаторы (SMA, RSI)
- ⚡ Автоматическое исполнение торговых сигналов
- 🛡️ Управление рисками (Take Profit / Stop Loss)
- 📉 **Частичное закрытие позиций** (AI может закрыть 50% позиции)
- 👁️ Отслеживание открытых позиций
- 📉 Генерация графиков и отчетов
- 📝 Двухуровневое логирование всех операций
- ✅ **Демо-режим** по умолчанию (безопасность)
- ✅ **Мультиактивность**: Forex, криптовалюты, акции
- ✅ **Автоматический мониторинг**: Закрытие позиций через 60 минут

---

## 🏗️ Архитектура системы

### Основные компоненты

```
OpenProducer/
- **`run.py`**: Точка входа в приложение.
- **`src/`**: Основной исходный код.
  - **`core/`**: Ядро системы.
    - **`collector.py`**: Сбор данных (цены, новости).
    - **`analyzer.py`**: Расчет технических индикаторов.
    - **`predict.py`**: Генерация сигналов с DeepSeek AI.
    - **`executor.py`**: Исполнение ордеров.
    - **`monitor.py`**: Мониторинг позиций.
    - **`plotter.py`**: Визуализация.
  - **`exchanges/`**: Клиенты бирж.
    - **`exchange_client.py`**: Базовый класс.
    - **`exchange_factory.py`**: Фабрика клиентов.
    - **`capital_client.py`**: Клиент Capital.com.
    - **`bingx_client.py`**: Клиент BingX.
  - **`utils/`**: Утилиты.
    - **`logger.py`**: Логирование.
    - **`config.py`**: Конфигурация.
    - **`news_api.py`**: Клиент новостей.
    - **`symbols.py`**: Управление символами.
```

### Пайплайн обработки данных

```
1. Сбор данных (src/core/collector.py) → Цены → data/prices/, Новости → data/news/
       ↓
2. Анализ (src/core/analyzer.py) → SMA, RSI → Промпты для AI
       ↓
3. AI-прогноз (src/core/predict.py) → DeepSeek API (Smart Skip: пропуск нейтральных рынков) → Торговые сигналы
       ↓
4. Исполнение (src/core/executor.py) → Capital.com API → Позиции с TP/SL
       ↓
5. Мониторинг (src/core/monitor.py) → Отслеживание → Автозакрытие (60 мин)
       ↓
6. Визуализация (src/core/plotter.py) → Графики → charts/
```

### Управление конфигурацией

- `MODE` - управляет демо vs реальной торговлей (по умолчанию "demo")
- Переменные окружения: `CAP_API_USERNAME`, `CAP_API_PASSWORD`, `DEEPSEEK_API_KEY`
- Торговые параметры: размер позиции (0.1 лота), TP (1.5%), SL (2.0%)
- Поддерживаемые символы: EUR/USD, BTC/USD (по умолчанию, можно добавить AAPL, GOLD, OIL до 5 макс)
- Максимум одновременных позиций: 5
- Таймаут позиции: 60 минут (автозакрытие)

---

## 🚀 Установка и настройка

### Требования
- Python 3.12+
- pip

### Установка зависимостей

```bash
# Основные зависимости
pip install pandas matplotlib python-dateutil requests

# Или через requirements.txt
pip install -r requirements.txt
```

**Основные зависимости:**
- `requests` - HTTP клиент для API
- `pandas` - Анализ данных
- `matplotlib` - Построение графиков
- `python-dateutil` - Работа с датами

### Настройка переменных окружения

**Создайте файл `.env` или экспортируйте переменные:**

```bash
# Выбор биржи (capital или bingx)
export EXCHANGE="bingx"

# BingX API (для торговли на BingX)
# Для VST (Demo) используйте ключи от VST аккаунта (если отличаются)
export BINGX_API_KEY="ваш_api_ключ_bingx"
export BINGX_SECRET_KEY="ваш_secret_key_bingx"

# Capital.com API (Legacy / Forex)
export CAP_API_USERNAME="ваш_email_демо_аккаунта"
export CAP_API_PASSWORD="ваш_пароль_демо_аккаунта"
export CAP_API_KEY="ваш_api_ключ_из_capital_com"

# DeepSeek API (для AI-анализа рынка)
export DEEPSEEK_API_KEY="ваш_ключ_deepseek"

# Режим работы (ВАЖНО: всегда demo для тестирования!)
export MODE="demo"

# Обработка новостей (true/false)
export ENABLE_NEWS="true"
```

**📋 Как получить CAP_API_KEY:**

1. Войдите в демо-аккаунт Capital.com
2. Перейдите в **Settings > API Integrations**
3. Нажмите **Generate API key**
4. Введите имя для ключа, установите пароль и срок действия
5. Введите 2FA код (если включен)
6. **Сохраните API ключ** (он показывается только один раз!)
7. Установите переменную окружения:
   ```bash
   export CAP_API_KEY="ваш_api_ключ_здесь"
   ```

⚠️ **ВАЖНО:** API ключ Capital.com отображается только один раз при создании!


**Загрузка из .env файла:**

```bash
# Сделайте .env файл исполняемым
chmod +x .env
source .env
```

### Проверка настройки

```bash
# Проверьте переменные окружения
echo $CAP_API_USERNAME
echo $DEEPSEEK_API_KEY

# Проверьте синтаксис файлов
python3 -m py_compile run.py src/**/*.py

# Проверьте импорты
python3 -c "from src.utils import logger, config; print('✅ Все OK!')"
```

---

## 🎯 Запуск системы

### Полный цикл торговли (разовый запуск)

```bash
python3 run.py
```

**Что произойдет:**
1. ✅ Проверка предварительных условий (API ключи, доступность)
2. 📊 Сбор данных о ценах (50 последних свечей 5-минутного таймфрейма)
3. 📰 Генерация синтетических новостей с тональностью
4. 🔍 Расчет технических индикаторов (SMA, RSI)
5. 🧠 Отправка данных в DeepSeek для анализа и получения сигналов
6. ⚡ Исполнение торговых сигналов (если confidence > 0.6)
7. 👁️ Мониторинг открытых позиций
8. 📈 Генерация графиков с индикаторами

### Пошаговый запуск (для тестирования)

```bash
# 1. Сбор данных
python3 -m src.core.collector

# 2. Технический анализ
python3 -m src.core.analyzer

# 3. AI-прогноз
python3 -m src.core.predict

# 4. Исполнение ордеров (требует настроенных API)
python3 -m src.core.executor

# 5. Мониторинг
python3 -m src.core.monitor

# 6. Генерация графиков
python3 -m src.core.plotter
```

### Команды для разработки

```bash
# Проверка синтаксиса всех файлов
python3 -m py_compile run.py src/**/*.py

# Тест импортов модулей
python3 -c "from src.utils import logger, config; print('OK')"

# Мониторинг логов в реальном времени
tail -f data/steps.log      # Все события системы
tail -f data/trades.log    # Только торговые операции

# Поиск ошибок
grep ERROR data/steps.log
```

---

## ⏰ Настройка постоянного мониторинга

### 🎯 Вариант 1: Cron (рекомендуется)

**Автоматический перезапуск через cron (Linux/Mac):**

```bash
# Создайте скрипт автозапуска run_trading_bot.sh
#!/bin/bash
cd /path/to/OpenProducer
source .env
python3 run.py >> data/cron.log 2>&1
echo "$(date): Trading bot cycle completed" >> data/cron.log

# Сделайте скрипт исполняемым
chmod +x run_trading_bot.sh

# Добавьте в crontab (запуск каждые 10 минут)
crontab -e
# Добавьте строку: */10 * * * * /path/to/run_trading_bot.sh
```

### 🎯 Вариант 2: Systemd сервис (Linux)

**Создайте файл сервиса `/etc/systemd/system/trading-bot.service`:**

```ini
[Unit]
Description=OpenProducer Trading Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/OpenProducer
ExecStart=/usr/bin/python3 /path/to/run.py
Restart=always
RestartSec=300

[Install]
WantedBy=multi-user.target
```

**Активируйте сервис:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot.service
sudo systemctl start trading-bot.service
sudo systemctl status trading-bot.service
```

### 🎯 Вариант 3: Screen/Tmux

**Использование screen:**

```bash
# Создаем сессию
screen -S trading-bot
python3 run.py
# Отключаемся: Ctrl+A, затем D
# Подключиться: screen -r trading-bot
```

### 🎯 Вариант 4: Docker (опционально)

**Создайте Dockerfile:**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python3", "run.py"]
```

**Соберите и запустите:**

```bash
docker build -t openproducer-bot .
docker run -d --name trading-bot --env-file .env \
  -v /path/to/data:/app/data \
  openproducer-bot
docker logs -f trading-bot
```

---

## 📊 Мониторинг и логи

### Файлы логов

**data/steps.log** - Все события системы
```
2025-11-02 20:11:27 | INFO | code | 📊 ШАГ 1: Сбор данных
2025-11-02 20:11:28 | ERROR | code | ❌ Ошибка: Connection timeout
```

**data/trades.log** - Только торговые операции
```
2025-11-02 20:11:29 | 📌 EUR/USD: открыт ордер BUY по 1.0850
2025-11-02 20:11:35 | ✅ Позиция abc123 закрыта
```

### Просмотр логов в реальном времени

```bash
# Все события системы
tail -f data/steps.log

# Только торговые операции
tail -f data/trades.log

# Поиск ошибок
grep ERROR data/steps.log

# Поиск торговых операций
grep "📌\|✅\|❌\|⏰" data/trades.log

# Статистика за день
grep "2025-11-02" data/trades.log | wc -l

# Количество строк в логах
wc -l data/steps.log data/trades.log
```

---

## ⚙️ Настройка параметров

### Файл bot_config.json

Все настройки торговых параметров теперь вынесены в файл `bot_config.json` в корне проекта.

```json
{
    "EXCHANGE_SYMBOLS": {
        "capital": ["BTC/USD", "EUR/USD"],
        "bingx": ["BTC-USDT", "ETH-USDT"]
    },
    "POSITION_SIZE": 0.1,
    "TAKE_PROFIT_PERCENT": 1.5,
    "STOP_LOSS_PERCENT": 1.5,
    "MIN_CONFIDENCE_THRESHOLD": 0.7,
    "DEFAULT_HOLD_TIME_MINUTES": 60
}
```

Вы можете изменять эти параметры без редактирования кода.

### Добавление новых активов

```json
"EXCHANGE_SYMBOLS": {
    "capital": ["BTC/USD", "EUR/USD", "AAPL", "GOLD", "BRENTOIL"],
    "bingx": ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
}
```
# Поддерживаются максимум 5 активов для каждой биржи!
```

---

## 🔧 Управление системой

### Остановка бота

```bash
# Если запущен через cron - просто удалите из crontab
crontab -e
# Удалите строку с запуском бота

# Если запущен через systemd
sudo systemctl stop trading-bot.service

# Если запущен через screen
screen -X -S trading-bot quit

# Если запущен через Docker
docker stop trading-bot
```

### Перезапуск бота

```bash
# Через systemd
sudo systemctl restart trading-bot.service

# Через screen
screen -X -S trading-bot quit
screen -S trading-bot python3 run.py

# Через Docker
docker restart trading-bot
```

---

## 🔧 Тестирование и отладка

### Тестирование компонентов

```bash
# Проверка синтаксиса всех Python файлов
# Проверка синтаксиса всех Python файлов
python3 -m py_compile run.py src/**/*.py

# Тест импортов
python3 -c "from src.utils import logger, config; print('✅ OK')"

# Тест отдельных модулей
python3 -m src.core.collector  # Тест сбора данных
python3 -m src.core.analyzer   # Тест анализа
python3 -m src.core.executor   # Тест управления позициями
```

### API Integration Details

**Capital.com API**
- Demo Base URL: `https://demo-api-capital.backend-capital.com/api/v1/`
- Real Base URL: `https://api-capital.backend-capital.com/api/v1/`
- Authentication: CST + Security Token (cached for 10 minutes)
- Endpoints: /session, /prices/{epic}, /positions, /positions/otc
- Epic mapping: EUR/USD → CS.D.EURUSD.TODAY.IP, BTC/USD → Crypto.BTCUSD

**BingX API**
- Demo (VST) Base URL: `https://open-api-vst.bingx.com`
- Real (Standard Futures) Base URL: `https://open-api.bingx.com`
- Authentication: HMAC SHA256 signing (Manual query construction)
- Endpoints: /openApi/swap/v2/user/balance, /openApi/swap/v3/quote/klines, /openApi/swap/v2/trade/order
- Symbol mapping: BTC/USD → BTC-USDT
- **Features**: Partial Close supported (via `percentage` param)

**DeepSeek API**
- Endpoint: `https://api.deepseek.com/v1/chat/completions`
- Model: deepseek-chat
- Temperature: 0.3 (for consistent predictions)

---

## 🔍 Troubleshooting

### Часто задаваемые вопросы

**Q: Можно ли использовать реальные деньги?**
**A:** НЕТ! Система предназначена только для демо-тестирования. Использование реальных денег крайне рискованно!

**Q: Сколько можно заработать?**
**A:** Система создана для образовательных целей. НЕ ожидайте прибыли. Результаты прошлых сделок не гарантируют будущих результатов.

**Q: Что делать если бот не запускается?**
**A:**
1. Проверьте переменные окружения: `echo $CAP_API_KEY`
2. Проверьте логи: `tail -f data/steps.log`
3. Убедитесь что аккаунт Capital.com активен
4. Проверьте статус DeepSeek API

### Частые проблемы

**1. Ошибка авторизации Capital.com**

*HTTP 400 ошибка:*
```
❌ HTTP 400 ошибка при авторизации
```
**Решение:**
- Проверьте CAP_API_KEY (ОБЯЗАТЕЛЬНО!)
- Получите API ключ в Settings > API Integrations на Capital.com
- Убедитесь, что API ключ активен

*HTTP 401 ошибка:*
```
❌ Ошибка авторизации: Invalid credentials
```
**Решение:**
- Проверьте CAP_API_USERNAME и CAP_API_PASSWORD
- В демо-режиме система использует https://demo-api-capital.backend-capital.com/api/v1/

**2. Ошибка DeepSeek API**
```
❌ Ошибка DeepSeek API: 401 Unauthorized
```
**Решение:** Проверьте DEEPSEEK_API_KEY

**3. Сессия устарела**
```
🔄 Сессия устарела, перезапускаем инициализацию
```
**Это нормально** - система автоматически перезапустит сессию

### Основные проблемы

1. **401 Unauthorized**: Система автовосстановления переинициализирует сессию
2. **Пустые данные о ценах**: Проверьте статус Capital.com API и epic mappings
3. **Ошибки DeepSeek**: Проверьте API ключ и доступность модели
4. **Ошибки импорта**: Убедитесь что установлены все зависимости (pandas, matplotlib, requests)

---

## 📁 Структура файлов

```
OpenProducer/
├── data/
│   ├── steps.log              # Логи выполнения кода
│   ├── trades.log            # Логи торговых операций
│   ├── prices/               # Сырые данные о ценах
│   │   ├── EUR/USD.json
│   │   └── BTC/USD.json
│   └── news/                 # Данные новостей
│       ├── EUR/USD.json
│       └── BTC/USD.json
├── charts/                   # Сгенерированные графики
│   └── EUR/USD_20251102_2015.png
├── run.py                    # Точка входа
├── src/                      # Исходный код
│   ├── core/                 # Основная логика
│   ├── exchanges/            # Клиенты бирж
│   └── utils/                # Утилиты
├── tests/                    # Интеграционные тесты
│   ├── test_bingx_actions.py
│   └── test_partial_close.py
├── scripts/                  # Скрипты (bash)
└── *.md                      # Документация
```

---

## 📋 Отчеты и документация

В проекте созданы подробные отчеты:

### Основная документация

1. **README_RU.md** - Полное руководство на русском языке
2. **CLAUDE.md** - Техническая документация для разработчиков
3. **COMMANDS.md** - Быстрые команды и шпаргалка

### Скрипты

1. **run_trading_bot.sh** - Автозапуск бота
2. **setup_cron.sh** - Настройка cron
3. **monitor_logs.sh** - Мониторинг логов

---

## 🔒 Безопасность

### Рекомендации

1. **Никогда не коммитьте API ключи** в git
2. **Используйте переменные окружения**, а не .env в продакшене
3. **Всегда тестируйте в demo режиме** перед реальной торговлей
4. **Мониторьте логи** для выявления подозрительной активности

### Ограничения

- Максимум 5 одновременных позиций
- Только лимитные ордера с TP/SL
- Автоматическое закрытие позиций через 60 минут

---

## 📈 Мониторинг производительности

### Ключевые метрики

- Количество открытых позиций
- Прибыльность сделок
- Время удержания позиций
- Частота сигналов

### Автоматические действия

- Закрытие по Take Profit/Stop Loss
- Закрытие позиций старше 60 минут
- Логирование всех операций

---

## 🎓 Образовательная ценность

Проект демонстрирует:

1. **Архитектуру ПО** - модульная структура
2. **API интеграцию** - работа с REST API
3. **Логирование** - централизованная система
4. **Обработку ошибок** - безопасные операции
5. **AI интеграцию** - машинное обучение в трейдинге
6. **Риск-менеджмент** - автоматическое управление

---

## ⚠️ ВАЖНЫЕ ЗАМЕЧАНИЯ

### 🔒 Безопасность
- Никогда не коммитьте API ключи в git
- Используйте переменные окружения
- Регулярно меняйте пароли

### 📊 Торговые риски
- ВСЕГДА тестируйте в demo режиме
- Никогда не используйте заемные средства
- Инвестируйте только те деньги, которые можете потерять
- Помните: прошлые результаты не гарантируют будущих

### 📞 Поддержка
- Изучите документацию: README.md, CLAUDE.md
- Проверьте логи при проблемах
- Изучите отчеты об ошибках
- Настройте уведомления о критических ошибках

---

## 🚨 Дисклеймер

**ВНИМАНИЕ:** Торговля на финансовых рынках связана с высокими рисками.  
Данная система предназначена для образовательных целей.  
Автоматическая торговля может привести к значительным убыткам.  
Используйте только собственные средства и на свой риск.

**Никогда не торгуйте деньгами, которые не можете позволить себе потерять!**

---

**Версия:** 1.0  
**Автор:** Claude Code  
**Дата:** 2025-11-02

---

## 🎯 Быстрый старт

```bash
# 1. Установить зависимости
pip install pandas matplotlib python-dateutil requests

# 2. Настроить переменные
export CAP_API_USERNAME="your_email"
export CAP_API_PASSWORD="your_password"
export DEEPSEEK_API_KEY="your_key"
export MODE="demo"

# 3. Запустить
python3 run.py

# 4. Настроить автозапуск (опционально)
./setup_cron.sh

# 5. Мониторить логи
./monitor_logs.sh
```

**Удачи в изучении! 🚀**

**НЕ РИСКУЙТЕ СВОИМИ ДЕНЬГАМИ! ИСПОЛЬЗУЙТЕ ТОЛЬКО DEMO-РЕЖИМ! 🚀**
