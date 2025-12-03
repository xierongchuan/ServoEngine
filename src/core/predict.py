import json
import requests
import time
from src.config import DEEPSEEK_API_KEY
from src.utils.logger import info, error

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
        from config import DEFAULT_HOLD_TIME_MINUTES
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
            from config import DEFAULT_HOLD_TIME_MINUTES
            data["hold_minutes"] = DEFAULT_HOLD_TIME_MINUTES
            
        # Добавляем причину по умолчанию
        if "reason" not in data:
            data["reason"] = "Автоматический сигнал"
            
        return data
    except Exception as e:
        error(f"❌ Ошибка парсинга ответа: {str(e)}")
        from config import DEFAULT_HOLD_TIME_MINUTES
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

    # 2. Фильтр по RSI (Нейтральная зона 40-60)
    # Если RSI в середине, и нет позиции -> скорее всего флэт, просто держим
    if 40 <= rsi <= 60:
        # Дополнительная проверка: Цена прилипла к SMA? (в пределах 0.5%)
        if abs(current_price - sma) / sma < 0.005:
            info(f"💤 {symbol}: Нейтральный рынок (RSI={rsi}, Цена~SMA) -> Пропуск ИИ (Auto-HOLD)")
            return False
        
        info(f"💤 {symbol}: RSI в нейтральной зоне ({rsi}) -> Пропуск ИИ (Auto-HOLD)")
        return False

    # Если условия выше не сработали (RSI < 40 или RSI > 60) -> Вызываем ИИ
    info(f"⚡ {symbol}: Активный рынок (RSI={rsi}) -> Вызываем ИИ")
    return True

def main(analyses):
    """Основная функция прогнозирования"""
    predictions = []
    from src.config import DEFAULT_HOLD_TIME_MINUTES

    for analysis in analyses:
        # Технический пре-фильтр
        if not should_call_ai(analysis):
            predictions.append({
                **analysis,
                "action": "hold",
                "confidence": 0.0,
                "hold_minutes": DEFAULT_HOLD_TIME_MINUTES,
                "reason": f"Auto-HOLD: Нейтральный рынок (RSI={analysis['rsi']})"
            })
            continue

        info(f"🧠 Генерация прогноза для {analysis['symbol']}...")
        response = get_prediction(analysis["prompt"])

        # Логируем очищенный ответ от DeepSeek (без markdown)
        if isinstance(response, str):
            import re
            cleaned = re.sub(r'```json\s*', '', response)
            cleaned = re.sub(r'```', '', cleaned)
            # Показываем первые 500 символов очищенного ответа
            info(f"📨 Ответ DeepSeek: {cleaned[:500]}{'...' if len(cleaned) > 500 else ''}")
        else:
            info(f"📨 Ответ DeepSeek: (dict) {response}")

        prediction = parse_response(response)
        
        predictions.append({
            **analysis,
            "action": prediction["action"],
            "confidence": prediction["confidence"],
            "hold_minutes": prediction["hold_minutes"],
            "reason": prediction["reason"]
        })
        
        # Задержка между запросами к API
        time.sleep(1)
    
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