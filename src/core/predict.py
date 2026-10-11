import json
import time
from src import config as runtime_config
from src.config import VALIDATION
from src.ai_settings import resolve_ai_role, migrate_ai_settings, AISettingsError
from src.ai.client import complete, AIProviderError
from src.utils.logger import info, error, warning


def get_prediction(prompt, model=None, max_tokens=None, temperature=None, *, role=None, config=None):
    """Запрос к актуальному провайдеру; при любой ошибке безопасный HOLD."""
    try:
        settings = runtime_config.get_current_ai_settings(config)
        selected = resolve_ai_role(settings, role)
        if model is not None:
            selected["model"] = model
        if max_tokens is not None:
            selected["max_tokens"] = max_tokens
        if temperature is not None:
            selected["temperature"] = temperature
        migrate_ai_settings({"provider": selected["provider"], "model": selected["model"],
                             "temperature": selected["temperature"], "max_tokens": selected["max_tokens"]})
        return complete(selected, prompt, use_fallbacks=role is None and model is None)
    except (AIProviderError, AISettingsError) as exc:
        code = exc.code if isinstance(exc, AIProviderError) else "invalid_settings"
        error(f"❌ Запрос AI отклонён: код={code}", category="llm")
    except Exception:
        code = "internal_error"
        error("❌ Внутренняя ошибка запроса AI", category="llm")
    return {"action": "hold", "confidence": 0.0, "reason": f"Ошибка API: {code}"}


def parse_response(response):
    """Парсит ответ AI в структурированный формат"""
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
        if "reason" not in data:
            data["reason"] = ""

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
            except (ValueError, TypeError):
                data["stop_loss"] = None
        else:
            data["stop_loss"] = None

        if "take_profit" in data:
            try:
                data["take_profit"] = float(data["take_profit"])
            except (ValueError, TypeError):
                data["take_profit"] = None

        return data
    except Exception as e:
        error(f"❌ Ошибка парсинга ответа: {str(e)}")
        return {
            "action": "hold",
            "confidence": 0.0,
            "reason": "Ошибка парсинга ответа AI"
        }

def smart_filter(analysis, config=None):
    """
    Интеллектуальный фильтр для экономии токенов.
    Возвращает (bool, str):
    - True, "Reason": если нужно вызвать ИИ.
    - False, "Reason": если можно пропустить (Auto-HOLD).
    """
    from src.config import (
        AI_THRESHOLDS,
        ENABLE_AI_SKIP_ON_RSI,
        MOMENTUM_STRATEGY,
        TECHNICAL_ANALYSIS,
        BOT_CONFIG as bot_config # Import full config to access dynamic keys
    )

    if config is not None:
        bot_config = config
        AI_THRESHOLDS = config.get("AI_THRESHOLDS", AI_THRESHOLDS)
        ENABLE_AI_SKIP_ON_RSI = config.get("ENABLE_AI_SKIP_ON_RSI", ENABLE_AI_SKIP_ON_RSI)
        MOMENTUM_STRATEGY = config.get("MOMENTUM_STRATEGY", MOMENTUM_STRATEGY)
        TECHNICAL_ANALYSIS = config.get("TECHNICAL_ANALYSIS", TECHNICAL_ANALYSIS)
    analysis_rsi = analysis["rsi"]
    analysis["current_price"]
    volume_ratio = analysis.get("volume_ratio", 1.0)
    has_position = analysis.get("has_position", False)

    # 1. Если есть открытая позиция - ВСЕГДА вызываем ИИ (нужен менеджмент)
    if has_position:
        return True, "Открытая позиция (Management)"

    # 2. Low Volume Filter (Low Cost: < 0.4x)
    # Default to True if not present to maintain backward compatibility, BUT user requested control.
    enable_low_vol_filter = bot_config.get("ENABLE_LOW_VOLUME_FILTER", False)

    _vol_thresholds = TECHNICAL_ANALYSIS.get("volume_thresholds", {})
    _low_vol = _vol_thresholds.get("low", 0.5)
    if enable_low_vol_filter and volume_ratio < _low_vol:
        # Ignore filter if RSI is extreme (possible reversal)
        if 25 < analysis_rsi < 75:
            return False, f"Low Volume ({volume_ratio:.2f}x) -> Auto-HOLD (Save Cost)"

    # 3. Базовые пороги
    rsi_min = AI_THRESHOLDS.get("RSI_NEUTRAL_MIN", 45)
    rsi_max = AI_THRESHOLDS.get("RSI_NEUTRAL_MAX", 55)
    rsi_overbought = AI_THRESHOLDS.get("RSI_OVERBOUGHT", 70)
    rsi_oversold = AI_THRESHOLDS.get("RSI_OVERSOLD", 30)

    # 3. Momentum Strategy Check (Особые условия)
    momentum_enabled = MOMENTUM_STRATEGY.get("enabled", False)

    # Пытаемся извлечь данные о Volume и Candles из prompt (так как они не передаются напрямую в analysis dict)
    # Это "хак", но он эффективен, чтобы не переписывать весь analyzer.py
    prompt_text = analysis.get("prompt", "")

    # Clean Volume Check
    _momentum_vol = TECHNICAL_ANALYSIS.get("momentum_volume_threshold", 1.2)
    high_volume = volume_ratio > _momentum_vol

    # Проверка на тренд (Global=UP, Local=BULLISH)
    strong_uptrend = "Global) | UP" in prompt_text and "Local) | BULLISH" in prompt_text
    strong_downtrend = "Global) | DOWN" in prompt_text and "Local) | BEARISH" in prompt_text

    # --- ЛОГИКА SKIP / CALL ---

    # A. RSI HIGH (> 70)
    if analysis_rsi > rsi_overbought:
        if momentum_enabled and strong_uptrend and high_volume:
            return True, f"Momentum Breakout Potential (RSI={analysis_rsi}, Vol=High)"
        if ENABLE_AI_SKIP_ON_RSI:
            return False, f"RSI Overbought ({analysis_rsi}) & No Momentum -> Auto-HOLD"

    # B. RSI LOW (< 30)
    if analysis_rsi < rsi_oversold:
        if momentum_enabled and strong_downtrend and high_volume:
            return True, f"Momentum Breakdown Potential (RSI={analysis_rsi}, Vol=High)"
        if ENABLE_AI_SKIP_ON_RSI:
            return False, f"RSI Oversold ({analysis_rsi}) & No Momentum -> Auto-HOLD"

    # C. NEUTRAL ZONE (45-55)
    if rsi_min <= analysis_rsi <= rsi_max:
        # Если есть тренд (UP/DOWN) -> Call AI (Pullback search)
        if strong_uptrend:
            return True, "Neutral RSI + Uptrend (Possible Pullback)"
        if strong_downtrend:
            return True, "Neutral RSI + Downtrend (Possible Pullback)"

        # Если флэт -> Skip ONLY if Skip is ENABLED
        if ENABLE_AI_SKIP_ON_RSI:
            return False, f"Neutral RSI ({analysis_rsi}) & Choppy/Flat -> Auto-HOLD"

    # D. DEFAULT (Active Market)
    return True, f"Active Market (RSI={analysis_rsi})"

def should_call_ai(analysis, config=None):
    """Wrapper для совместимости, вызывает smart_filter и логирует результат."""
    should_call, reason = smart_filter(analysis, config=config)

    symbol = analysis["symbol"]
    if should_call:
        if "Management" in reason:
            info(f"⚠️ {symbol}: {reason} -> Вызываем ИИ")
        elif "Momentum" in reason:
            info(f"🔥 {symbol}: {reason} -> Вызываем ИИ")
        else:
            info(f"⚡ {symbol}: {reason} -> Вызываем ИИ")
    else:
        info(f"💤 {symbol}: {reason} -> Пропуск ИИ")

    return should_call, reason

def process_analysis(analysis, config=None):
    """Прогноз только для пригодного source/instance snapshot, без повторного чтения legacy цен."""
    from src.core.data.reader import runtime_data_config, validate_analysis_market_data
    config = runtime_data_config(config)
    if not validate_analysis_market_data(analysis, config):
        warning(f"⚠️ {analysis.get('symbol')}: прогноз отклонён — устаревшие или чужие рыночные данные")
        return {**analysis, "action": "hold", "confidence": 0.0, "reason": "Непригодный market-data snapshot", "execution_allowed": False}

    # Технический пре-фильтр
    should_call, skip_reason = should_call_ai(analysis, config=config)
    if not should_call:
        return {
            **analysis,
            "action": "hold",
            "confidence": 0.0,
            "reason": skip_reason
        }

    info(f"🧠 Генерация прогноза для {analysis['symbol']}...")
    # Retry logic for API/Parsing errors AND Logic Validation
    max_retries = 2
    current_prompt = analysis["prompt"]
    final_prediction = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            info(f"🔄 Повторная попытка (Logic/API Retry) {attempt}/{max_retries}...")

        response = get_prediction(current_prompt, config=config)
        info(f"📨 Запрос AI завершён ({analysis['symbol']}, попытка {attempt+1})", category="llm")

        prediction = parse_response(response)

        # 1. Check Technical Errors (API/Parsing)
        reason = prediction.get("reason", "")
        if reason == "Ошибка парсинга ответа AI" or reason.startswith("Ошибка API"):
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

    # Enforce min_hold_hours: block close signals if force_hold is set
    if analysis.get("force_hold", False):
        if final_prediction["action"] in ("close", "close_partial"):
            info(f"🔒 {analysis['symbol']}: Close signal blocked by min_hold_hours. Forcing HOLD.")
            final_prediction = {
                "action": "hold",
                "confidence": final_prediction["confidence"],
                "reason": f"Min hold not reached. Original: {final_prediction['action']}",
                "stop_loss": final_prediction.get("stop_loss"),
                "take_profit": final_prediction.get("take_profit"),
            }

    if not validate_analysis_market_data(analysis, config):
        warning(f"⚠️ {analysis.get('symbol')}: данные устарели во время AI-запроса")
        return {**analysis, "action": "hold", "confidence": 0.0, "reason": "Snapshot устарел во время AI-запроса", "execution_allowed": False}
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
    from src.config import AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS

    # Determine which R/R ratio to use
    if AGGRESSIVE_MODE:
        AGGRESSIVE_SETTINGS.get("MIN_RISK_REWARD_RATIO", 1.0)

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
        warning("⚠️ SL равен текущей цене. Меняем на HOLD.")
        prediction["action"] = "hold"
        prediction["reason"] += " [AUTO-FIX: Zero Risk]"
        return prediction

    rr_ratio = reward / risk

    # R/R validation
    soft_limit = VALIDATION.get("rr_soft_limit", 0.5)
    action = prediction.get("action", "hold")

    if rr_ratio <= soft_limit:
        if action in ("buy", "sell"):
            # Auto-correct TP to meet minimum R/R instead of blocking
            required_reward = risk * soft_limit
            if action == "buy":
                new_tp = current_price + required_reward
            else:
                new_tp = current_price - required_reward

            warning(f"⚠️ Low R/R ({rr_ratio:.2f} < {soft_limit}) for {action.upper()}. "
                    f"Adjusting TP: {take_profit:.2f} → {new_tp:.2f} (R/R → {soft_limit:.2f})")
            prediction["take_profit"] = new_tp
            prediction["reason"] += f" [AUTO-FIX: TP adjusted for R/R {rr_ratio:.2f}→{soft_limit:.2f}]"
        else:
            # For hold/close, just warn
            warning(f"⚠️ Низкий Risk/Reward ({rr_ratio:.2f}) на SL/TP update. Applying anyway.")

    return prediction

def main(analyses, config=None):
    """Прогнозы используют тот же instance/source config, что анализ."""
    from src.config import ENABLE_PARALLEL_PROCESSING
    from src.core.data.reader import runtime_data_config
    config = runtime_data_config(config)
    ENABLE_PARALLEL_PROCESSING = config.get("ENABLE_PARALLEL_PROCESSING", ENABLE_PARALLEL_PROCESSING)
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
            future_to_symbol = {executor.submit(process_analysis, analysis, config=config): analysis['symbol'] for analysis in analyses}

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
                result = process_analysis(analysis, config=config)
                predictions.append(result)
            except Exception as exc:
                error(f"❌ Ошибка при обработке {analysis['symbol']}: {exc}")

    return predictions

if __name__ == "__main__":
    import sys
    import json
    import analyzer

    info("🔄 Запуск прогнозирования...")

    # Если запускается через пайплайн
    if not sys.stdin.isatty():
        analyses = json.load(sys.stdin)
    else:
        analyses = analyzer.main()

    predictions = main(analyses)
    info("\n✅ Прогнозы готовы:")
    print(json.dumps(predictions, indent=2))
