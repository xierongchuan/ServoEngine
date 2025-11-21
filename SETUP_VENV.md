# Настройка виртуального окружения

## Установка и настройка

### 1. Создание виртуального окружения
```bash
python3 -m venv venv
```

### 2. Активация окружения
```bash
# Для Linux/Mac:
source venv/bin/activate

# Или используйте скрипт:
./run_trading_bot.sh
```

### 3. Установка зависимостей
```bash
source venv/bin/activate
pip install -r requirements.txt
```

## Структура проекта

```
OpenProducer/
├── venv/                    # Виртуальное окружение
├── requirements.txt          # Список зависимостей
├── run_trading_bot.sh       # Скрипт запуска (использует venv)
└── *.py                     # Модули проекта
```

## Запуск торгового бота

### Через скрипт (рекомендуется)
```bash
./run_trading_bot.sh
```

### Вручную
```bash
source venv/bin/activate
python3 main.py
```

## Проверка установки

```bash
source venv/bin/activate
python3 -c "from newspaper import Article; print('✅ OK')"
```

## Зависимости

- `requests` - HTTP запросы
- `pandas` - Обработка данных
- `matplotlib` - Построение графиков
- `textblob` - Анализ тональности
- `newspaper3k` - Извлечение полного текста новостей
- `lxml_html_clean` - Парсинг HTML
- `nltk` - Обработка текста
