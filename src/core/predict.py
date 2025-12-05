import json
import requests
import time
from src.config import DEEPSEEK_API_KEY
from src.utils.logger import info, error, warning

def get_prediction(prompt):
    """Отправляет промпт в DeepSeek и получает ответ"""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 256,
        "temperature": 0.3
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        # Проверяем структуру ответа
        if "choices" not in data or not data["choices"]:
            raise ValueError("Некорректный ответ DeepSeek API: нет поля choices")

        content = data["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("Некорректный ответ DeepSeek API: пустой content")

        return content
    except Exception as e:
        error(f"❌ Ошибка DeepSeek API: {str(e)}")
        # Возвращаем dict вместо строки JSON
        from src.config import DEFAULT_HOLD_TIME_MINUTES
        return {
            "action": "hold",
            "confidence": 0.0,
            "hold_minutes": DEFAULT_HOLD_TIME_MINUTES,
            "reason": f"Ошибка API: {str(e)}"
        }

def parse_response(response):
    """Парсит ответ DeepSeek в структурированный формат"""
    try:
        # Если response уже dict (при ошибке API), возвращаем его
        if isinstance(response, dict):
            data = response.copy()
        else:
            # Очищаем ответ от markdown блоков ```json```
            # Удаляем все блоки кода с пометкой json
            import re
            cleaned = re.sub(r'```json\s*', '', response)
            cleaned = re.sub(r'```', '', cleaned)

            # Улучшенное извлечение JSON - ищем первую открывающую скобку и соответствующую закрывающую
            start = cleaned.find('{')
            if start == -1:
                raise ValueError("Нет JSON в ответе")

            # Считаем глубину скобок для поиска соответствующей закрывающей
            brace_count = 0
            end = -1
            for i in range(start, len(cleaned)):
                if cleaned[i] == '{':
                    brace_count += 1
                elif cleaned[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break

            if end == -1:
                raise ValueError("Некорректный JSON в ответе")

            json_str = cleaned[start:end]
            data = json.loads(json_str)

        # Валидация данных
        if "action" not in data:
            raise ValueError("Нет поля action")
        if "confidence" not in data:
            data["confidence"] = 0.5

        # Нормализация confidence
        data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))

        # Добавляем время удержания по умолчанию
        if "hold_minutes" not in data:
            from src.config import DEFAULT_HOLD_TIME_MINUTES
            data["hold_minutes"] = DEFAULT_HOLD_TIME_MINUTES

        # Добавляем причину по умолчанию
        # Добавляем процент закрытия (для close_partial)
        if "percentage" not in data:
            data["percentage"] = 1.0
        else:
            # Нормализация percentage
            if data["percentage"] is None:
                data["percentage"] = 1.0
            else:
                data["percentage"] = max(0.0, min(1.0, float(data["percentage"])))

        # Extract SL/TP
        if "stop_loss" in data:
            try:
                data["stop_loss"] = float(data["stop_loss"])
            except:
                data["stop_loss"] = None
        else:
            data["stop_loss"] = None

        if "take_profit" in data:
            try:
                data["take_profit"] = float(data["take_profit"])
            except:
                data["take_profit"] = None

        return data
    except Exception as e:
        error(f"❌ Ошибка парсинга ответа: {str(e)}")
        from src.config import DEFAULT_HOLD_TIME_MINUTES
        return {
            "action": "hold",
            "confidence": 0.0,
            "hold_minutes": DEFAULT_HOLD_TIME_MINUTES,
            "reason": "Ошибка парсинга ответа DeepSeek"
        }

def should_call_ai(analysis):
    """
    Определяет, нужно ли вызывать ИИ на основе технических индикаторов.
    Возвращает True, если нужен анализ ИИ.
    Возвращает False, если ситуация нейтральная (можно просто HOLD).
    """
    symbol = analysis["symbol"]
    has_position = analysis.get("has_position", False)
    rsi = analysis["rsi"]
    current_price = analysis["current_price"]
    sma = analysis["sma"]

    # 1. Если есть открытая позиция - ВСЕГДА вызываем ИИ (нужен менеджмент позиции)
    if has_position:
        info(f"⚠️ {symbol}: Есть открытая позиция -> Вызываем ИИ")
        return True

    # 2. Фильтр по RSI (Нейтральная зона)
    from src.config import AI_THRESHOLDS
    rsi_min = AI_THRESHOLDS.get("RSI_NEUTRAL_MIN", 45)
    rsi_max = AI_THRESHOLDS.get("RSI_NEUTRAL_MAX", 55)

    if rsi_min <= rsi <= rsi_max:
        from src.config import AGGRESSIVE_MODE

        # В Агрессивном режиме проверяем тренд
        if AGGRESSIVE_MODE:
            # Тренд ВВЕРХ (Цена > SMA) и RSI < 60 -> Возможен откат для покупки
            if current_price > sma and rsi < 55:
                info(f"🔥 {symbol}: Агрессивный режим. Тренд UP + RSI={rsi} -> Вызываем ИИ (Поиск отката)")
                return True

            # Тренд ВНИЗ (Цена < SMA) и RSI > 40 -> Возможен откат для продажи
            if current_price < sma and rsi > 45:
                info(f"🔥 {symbol}: Агрессивный режим. Тренд DOWN + RSI={rsi} -> Вызываем ИИ (Поиск отката)")
                return True

        # Стандартная логика (или если тренд не подтвержден в агрессивном режиме)
        # Дополнительная проверка: Цена прилипла к SMA? (в пределах 0.5%)
        if abs(current_price - sma) / sma < 0.005:
            info(f"💤 {symbol}: Нейтральный рынок (RSI={rsi}, Цена~SMA) -> Пропуск ИИ (Auto-HOLD)")
            return False

        info(f"💤 {symbol}: RSI в нейтральной зоне ({rsi}) -> Пропуск ИИ (Auto-HOLD)")
        return False

    # Если условия выше не сработали (RSI < 40 или RSI > 60) -> Вызываем ИИ
    info(f"⚡ {symbol}: Активный рынок (RSI={rsi}) -> Вызываем ИИ")
    return True

def process_analysis(analysis):
    """Обрабатывает один анализ: проверяет условия, делает запрос к ИИ (с ретраями) и возвращает прогноз"""
    from src.config import DEFAULT_HOLD_TIME_MINUTES

    # Технический пре-фильтр
    if not should_call_ai(analysis):
        return {
            **analysis,
            "action": "hold",
            "confidence": 0.0,
            "hold_minutes": DEFAULT_HOLD_TIME_MINUTES,
            "reason": f"Auto-HOLD: Нейтральный рынок (RSI={analysis['rsi']})"
        }

    info(f"🧠 Генерация прогноза для {analysis['symbol']}...")
    # Retry logic for API/Parsing errors
    max_retries = 1
    prediction = None

    for attempt in range(max_retries + 1):
        response = get_prediction(analysis["prompt"])

        # Логируем очищенный ответ от DeepSeek (без markdown)
        if isinstance(response, str):
            import re
            cleaned = re.sub(r'```json\s*', '', response)
            cleaned = re.sub(r'```', '', cleaned)
            info(f"📨 Ответ DeepSeek ({analysis['symbol']}, Попытка {attempt+1}/{max_retries+1}): {cleaned}") # Truncate log
        else:
            info(f"📨 Ответ DeepSeek ({analysis['symbol']}, Попытка {attempt+1}/{max_retries+1}): (dict) {response}")

        prediction = parse_response(response)

        # Check if parsing failed (it returns a default dict with specific reason)
        if prediction["reason"] == "Ошибка парсинга ответа DeepSeek" or prediction["reason"].startswith("Ошибка API"):
            if attempt < max_retries:
                warning(f"⚠️ {analysis['symbol']}: Ошибка парсинга/API. Повторная попытка через 1 сек...")
                time.sleep(1)
                continue
            else:
                error(f"❌ {analysis['symbol']}: Не удалось получить корректный ответ после {max_retries+1} попыток.")
        else:
            # Success
            break



    # Валидация прогноза (Risk/Reward)
    validated_prediction = validate_prediction(prediction, analysis["current_price"])

    return {
        **analysis,
        "action": validated_prediction["action"],
        "confidence": validated_prediction["confidence"],
        "percentage": validated_prediction.get("percentage", 1.0),
        "stop_loss": validated_prediction.get("stop_loss"),
        "take_profit": validated_prediction.get("take_profit"),
        "hold_minutes": validated_prediction["hold_minutes"],
        "reason": validated_prediction["reason"]
    }

def validate_prediction(prediction, current_price):
    """
    Валидирует прогноз ИИ, проверяя Risk/Reward Ratio.
    Если R/R слишком низкий, меняет действие на HOLD или понижает уверенность.
    """
    from src.config import MIN_RISK_REWARD_RATIO

    action = prediction.get("action")
    stop_loss = prediction.get("stop_loss")
    take_profit = prediction.get("take_profit")

    # Проверяем только для сигналов на вход (BUY/SELL)
    if action not in ["buy", "sell"]:
        return prediction

    if not stop_loss or not take_profit:
        # Если нет SL/TP для входа - это ошибка, меняем на HOLD
        warning(f"⚠️ Отсутствует SL/TP для сигнала {action}. Меняем на HOLD.")
        prediction["action"] = "hold"
        prediction["reason"] += " [AUTO-FIX: Missing SL/TP]"
        return prediction

    # Расчет риска и прибыли
    risk = abs(current_price - stop_loss)
    reward = abs(current_price - take_profit)

    if risk == 0:
        warning(f"⚠️ SL равен текущей цене. Меняем на HOLD.")
        prediction["action"] = "hold"
        prediction["reason"] += " [AUTO-FIX: Zero Risk]"
        return prediction

    rr_ratio = reward / risk

    if rr_ratio < MIN_RISK_REWARD_RATIO:
        warning(f"⚠️ Низкий Risk/Reward ({rr_ratio:.2f} < {MIN_RISK_REWARD_RATIO}). Сигнал {action} отклонен.")
        prediction["action"] = "hold"
        prediction["reason"] += f" [AUTO-FIX: Low R/R ({rr_ratio:.2f})]"
        prediction["confidence"] = 0.0

    return prediction

def main(analyses):
    """Основная функция прогнозирования (Multi-threaded or Sequential)"""
    from src.config import ENABLE_PARALLEL_PROCESSING
    import concurrent.futures

    predictions = []

    if ENABLE_PARALLEL_PROCESSING:
        # Use ThreadPoolExecutor to run AI requests in parallel
        # Max workers = number of analyses or a reasonable limit (e.g., 10)
        max_workers = min(len(analyses), 10)
        if max_workers == 0:
            return []

        info(f"🚀 Запуск параллельного анализа для {len(analyses)} активов (потоков: {max_workers})...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_symbol = {executor.submit(process_analysis, analysis): analysis['symbol'] for analysis in analyses}

            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    result = future.result()
                    predictions.append(result)
                except Exception as exc:
                    error(f"❌ Ошибка при обработке {symbol}: {exc}")
    else:
        # Sequential execution
        info(f"🐌 Запуск последовательного анализа для {len(analyses)} активов...")
        for analysis in analyses:
            try:
                result = process_analysis(analysis)
                predictions.append(result)
            except Exception as exc:
                error(f"❌ Ошибка при обработке {analysis['symbol']}: {exc}")

    return predictions

if __name__ == "__main__":
    import sys, json, analyzer

    info("🔄 Запуск прогнозирования...")

    # Если запускается через пайплайн
    if not sys.stdin.isatty():
        analyses = json.load(sys.stdin)
    else:
        analyses = analyzer.main()

    predictions = main(analyses)
    info("\n✅ Прогнозы готовы:")
    print(json.dumps(predictions, indent=2))
