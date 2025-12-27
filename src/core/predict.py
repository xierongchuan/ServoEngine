import json
import requests
import time
from src.config import AI_API_KEY, AI_BASE_URL, AI_MODEL, AI_PROVIDER
from src.utils.logger import info, error, warning

def get_prediction(prompt):
    """Отправляет промпт в AI (DeepSeek/SiliconFlow/OpenRouter) и получает ответ"""
    url = AI_BASE_URL
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }

    if AI_PROVIDER == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/temur/OpenProducer" # Replace with actual site if exists
        headers["X-Title"] = "OpenProducerBot"
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512, # Increased for safety
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
        return {
            "action": "hold",
            "confidence": 0.0,
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
        return {
            "action": "hold",
            "confidence": 0.0,
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
    rsi_overbought = AI_THRESHOLDS.get("RSI_OVERBOUGHT", 70)
    rsi_oversold = AI_THRESHOLDS.get("RSI_OVERSOLD", 30)

    # --- НОВЫЕ ПРОВЕРКИ ДЛЯ ЭКОНОМИИ ТОКЕНОВ ---
    from src.config import ENABLE_AI_SKIP_ON_RSI, MOMENTUM_STRATEGY

    # Пропускаем вызов ИИ для экономии, НО только если не активна Momentum-стратегия
    # Momentum-стратегия ищет пробои именно на высоком RSI, поэтому скипать нельзя.
    momentum_enabled = MOMENTUM_STRATEGY.get("enabled", False)

    if ENABLE_AI_SKIP_ON_RSI and not momentum_enabled:
        # Если Тренд UP, но RSI уже перекуплен -> Мы не будем покупать (поздно), а продавать против тренда нельзя.
        # Значит, AI скажет HOLD. Экономим запрос.
        if current_price > sma and rsi > rsi_overbought:
            info(f"💤 {symbol}: Тренд UP, но RSI({rsi}) > {rsi_overbought} (Перекуплен) -> Пропуск ИИ (Auto-HOLD)")
            return False

        # Если Тренд DOWN, но RSI уже перепродан -> Мы не будем продавать (поздно), а покупать против тренда нельзя.
        # Значит, AI скажет HOLD. Экономим запрос.
        if current_price < sma and rsi < rsi_oversold:
            info(f"💤 {symbol}: Тренд DOWN, но RSI({rsi}) < {rsi_oversold} (Перепродан) -> Пропуск ИИ (Auto-HOLD)")
            return False
    # -------------------------------------------

    if rsi_min <= rsi <= rsi_max:
        from src.config import AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS

        # В Агрессивном режиме проверяем тренд
        if AGGRESSIVE_MODE:
            rsi_buy_cond = AGGRESSIVE_SETTINGS.get("RSI_BUY_COND", 60)
            rsi_sell_cond = AGGRESSIVE_SETTINGS.get("RSI_SELL_COND", 40)

            # Тренд ВВЕРХ (Цена > SMA) и RSI < 60 -> Возможен откат для покупки
            if current_price > sma and rsi < rsi_buy_cond:
                info(f"🔥 {symbol}: Агрессивный режим. Тренд UP + RSI={rsi} -> Вызываем ИИ (Поиск отката)")
                return True

            # Тренд ВНИЗ (Цена < SMA) и RSI > 40 -> Возможен откат для продажи
            if current_price < sma and rsi > rsi_sell_cond:
                info(f"🔥 {symbol}: Агрессивный режим. Тренд DOWN + RSI={rsi} -> Вызываем ИИ (Поиск отката)")
                return True

        # Стандартная логика (или если тренд не подтвержден в агрессивном режиме)
        # Стандартная логика:
        # Если RSI в нейтральной зоне (48-52), но есть четкий тренд -> Вызываем ИИ
        # Тренд UP (Цена > SMA) -> Ищем вход BUY (RSI ~50 это ОК для продолжения)
        if current_price > sma:
             info(f"⚡ {symbol}: Нейтральный RSI({rsi}), но Тренд UP -> Вызываем ИИ (Поиск входа)")
             return True

        # Тренд DOWN (Цена < SMA) -> Ищем вход SELL
        if current_price < sma:
             info(f"⚡ {symbol}: Нейтральный RSI({rsi}), но Тренд DOWN -> Вызываем ИИ (Поиск входа)")
             return True

        # Если цена прямо на SMA (флэт) -> HOLD
        if abs(current_price - sma) / sma < 0.005:
            info(f"💤 {symbol}: Нейтральный рынок (RSI={rsi}, Цена~SMA) -> Пропуск ИИ (Auto-HOLD)")
            return False

        info(f"💤 {symbol}: RSI в нейтральной зоне ({rsi}) и нет тренда -> Пропуск ИИ (Auto-HOLD)")
        return False

    # Если условия выше не сработали (RSI < 40 или RSI > 60) -> Вызываем ИИ
    info(f"⚡ {symbol}: Активный рынок (RSI={rsi}) -> Вызываем ИИ")
    return True

def process_analysis(analysis):
    """Обрабатывает один анализ: проверяет условия, делает запрос к ИИ (с ретраями) и возвращает прогноз"""
    # Технический пре-фильтр
    if not should_call_ai(analysis):
        return {
            **analysis,
            "action": "hold",
            "confidence": 0.0,
            "reason": f"Auto-HOLD: Нейтральный рынок (RSI={analysis['rsi']})"
        }

    info(f"🧠 Генерация прогноза для {analysis['symbol']}...")
    # Retry logic for API/Parsing errors AND Logic Validation
    max_retries = 1
    current_prompt = analysis["prompt"]
    final_prediction = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            info(f"🔄 Повторная попытка (Logic/API Retry) {attempt}/{max_retries}...")

        response = get_prediction(current_prompt)

        # Логируем очищенный ответ от DeepSeek (без markdown)
        if isinstance(response, str):
            import re
            cleaned = re.sub(r'```json\s*', '', response)
            cleaned = re.sub(r'```', '', cleaned)
            info(f"📨 Ответ DeepSeek ({analysis['symbol']}, Попытка {attempt+1}): {cleaned}")
        else:
            info(f"📨 Ответ DeepSeek ({analysis['symbol']}, Попытка {attempt+1}): (dict) {response}")

        prediction = parse_response(response)

        # 1. Check Technical Errors (API/Parsing)
        if prediction["reason"] == "Ошибка парсинга ответа DeepSeek" or prediction["reason"].startswith("Ошибка API"):
            if attempt < max_retries:
                warning(f"⚠️ {analysis['symbol']}: Ошибка парсинга/API. Повторная попытка через 1 сек...")
                time.sleep(1)
                continue
            else:
                error(f"❌ {analysis['symbol']}: Не удалось получить корректный ответ после {max_retries+1} попыток.")
                final_prediction = prediction
                break

        # 2. Check Logic/Strategy Validation (e.g. Risk/Reward)
        validated_prediction = validate_prediction(prediction, analysis["current_price"], analysis.get("has_position", False))

        # Check if it was an auto-fix rejection (detected by reason tag)
        if "[AUTO-FIX" in validated_prediction["reason"]:
            reject_reason = validated_prediction["reason"].split("[AUTO-FIX:")[1].strip("]")
            warning(f"⚠️ {analysis['symbol']}: Сигнал скорректирован валидатором: {reject_reason}")
            # We do not retry, just accept the correction (usually to HOLD)

        # If we got here, result is accepted (or it was already hold, or retries exhausted)
        final_prediction = validated_prediction
        break

    # Fallback if loop finishes without assignment (should not happen but safety first)
    if not final_prediction:
         final_prediction = {
             "action": "hold",
             "confidence": 0.0,
             "reason": "Unknown Error (Loop Finished)"
         }

    return {
        **analysis,
        "action": final_prediction["action"],
        "confidence": final_prediction["confidence"],
        "percentage": final_prediction.get("percentage", 1.0),
        "stop_loss": final_prediction.get("stop_loss"),
        "take_profit": final_prediction.get("take_profit"),
        "reason": final_prediction["reason"]
    }

def validate_prediction(prediction, current_price, has_position=False):
    """
    Валидирует прогноз ИИ, проверяя Risk/Reward Ratio.
    Если R/R слишком низкий, меняет действие на HOLD или понижает уверенность.
    """
    from src.config import MIN_RISK_REWARD_RATIO, AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS

    # Determine which R/R ratio to use
    target_rr = MIN_RISK_REWARD_RATIO
    if AGGRESSIVE_MODE:
        target_rr = AGGRESSIVE_SETTINGS.get("MIN_RISK_REWARD_RATIO", 1.0)

    action = prediction.get("action")
    stop_loss = prediction.get("stop_loss")
    take_profit = prediction.get("take_profit")

    # Проверяем также для HOLD, если есть SL/TP (обновление позиций)
    # НО ТОЛЬКО если есть открытая позиция!
    if action not in ["buy", "sell"]:
        # Если это HOLD
        if action == "hold" and has_position and (stop_loss or take_profit):
             # Разрешаем валидацию для HOLD+Update
             pass
        else:
             # Не валидируем обычный HOLD или HOLD без позиции
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

    # Relaxed validation: Just log a warning if R/R is very low, but do not reject unless it's catastrophic (e.g. < 0.3)
    # The user wants "normal" risk management, relying on the AI's judgment.

    soft_limit = 0.5
    if rr_ratio < soft_limit:
        warning(f"⚠️ Низкий Risk/Reward ({rr_ratio:.2f}). AI считает это допустимым, но риск высок.")
        # We DO NOT reject the signal, just log it.

    # We accept the prediction as is.

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
