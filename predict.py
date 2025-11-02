import json
import requests
import time
from config import DEEPSEEK_API_KEY
from logger import info, error

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
        return {
            "action": "hold",
            "confidence": 0.0,
            "hold_minutes": 30,
            "reason": f"Ошибка API: {str(e)}"
        }

def parse_response(response):
    """Парсит ответ DeepSeek в структурированный формат"""
    try:
        # Если response уже dict (при ошибке API), возвращаем его
        if isinstance(response, dict):
            data = response.copy()
        else:
            # Извлекаем JSON из ответа
            start = response.find('{')
            end = response.rfind('}') + 1
            if start == -1 or end == 0:
                raise ValueError("Нет JSON в ответе")

            json_str = response[start:end]
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
            data["hold_minutes"] = 30
            
        # Добавляем причину по умолчанию
        if "reason" not in data:
            data["reason"] = "Автоматический сигнал"
            
        return data
    except Exception as e:
        error(f"❌ Ошибка парсинга ответа: {str(e)}")
        return {
            "action": "hold",
            "confidence": 0.0,
            "hold_minutes": 30,
            "reason": "Ошибка парсинга ответа DeepSeek"
        }

def main(analyses):
    """Основная функция прогнозирования"""
    predictions = []
    for analysis in analyses:
        info(f"🧠 Генерация прогноза для {analysis['symbol']}...")
        response = get_prediction(analysis["prompt"])

        # Безопасное логирование ответа (может быть dict или str)
        if isinstance(response, str):
            info(f"📨 Ответ DeepSeek: {response[:200]}...")
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