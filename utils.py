import requests
import time
from config import USERNAME, PASSWORD, CAP_API_KEY, API_BASE, MODE
from logger import info, error, warning

# Кэш для токенов авторизации
_cached_tokens = None
_tokens_cache_time = 0
TOKEN_CACHE_TTL = 600  # 10 минут (согласно Capital.com API)
_session_initialized = False  # Флаг инициализации сессии

def get_session_token():
    """Получает базовые токены через логин с email и API ключом"""
    global _cached_tokens, _tokens_cache_time

    # Проверяем кэш
    current_time = time.time()
    if _cached_tokens and (current_time - _tokens_cache_time) < TOKEN_CACHE_TTL:
        return _cached_tokens

    # Проверяем наличие API ключа
    if not CAP_API_KEY:
        error("❌ CAP_API_KEY не настроен! Получите API ключ в Settings > API Integrations на Capital.com")
        raise Exception("CAP_API_KEY не настроен. Создайте API ключ в настройках аккаунта Capital.com")

    url = f"{API_BASE}session"
    headers = {
        "X-CAP-API-KEY": CAP_API_KEY,  # ОБЯЗАТЕЛЬНО!
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "identifier": USERNAME,
        "password": PASSWORD,
        "encryptedPassword": False  # Используем обычный пароль, не зашифрованный
    }

    info(f"🔑 Попытка авторизации на {url}")
    info(f"   Username: {USERNAME[:3]}***")
    info(f"   API Key: {CAP_API_KEY[:8]}***")
    info(f"   Password: ****{'*' * (len(PASSWORD) - 4) if len(PASSWORD) > 4 else '*'}")

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)

        # Логируем детали ошибки
        if response.status_code >= 400:
            error(f"❌ HTTP {response.status_code} ошибка")
            error(f"   URL: {response.url}")
            error(f"   Request Headers: {dict(response.request.headers)}")
            error(f"   Request Body: {response.request.body}")
            error(f"   Response Headers: {dict(response.headers)}")

            try:
                error(f"   Response Body: {response.text[:500]}")
            except:
                error("   Response Body: (не удалось получить)")

            # Специальные сообщения для типичных ошибок
            if response.status_code == 400:
                error("\n💡 Возможные причины:")
                error("   1. Неверный API ключ (CAP_API_KEY)")
                error("   2. Неверные учетные данные (CAP_API_USERNAME/CAP_API_PASSWORD)")
                error("   3. API ключ заблокирован или истек")
                error("   4. Аккаунт заблокирован или приостановлен")

        response.raise_for_status()

        tokens = {
            "cst": response.headers.get("CST"),
            "security_token": response.headers.get("X-SECURITY-TOKEN")
        }

        # Проверяем, что токены получены
        if not tokens["cst"] or not tokens["security_token"]:
            error(f"❌ Сервер не вернул токены авторизации")
            error(f"   Headers: {dict(response.headers)}")
            raise ValueError("Сервер не вернул токены авторизации")

        info("✅ Авторизация успешна")
        info(f"   CST: {tokens['cst'][:10]}...")
        info(f"   X-SECURITY-TOKEN: {tokens['security_token'][:10]}...")

        # Кэшируем токены
        _cached_tokens = tokens
        _tokens_cache_time = current_time

        return tokens
    except requests.exceptions.RequestException as e:
        error(f"❌ Ошибка сетевого запроса: {str(e)}")
        raise Exception(f"Ошибка авторизации (сеть): {str(e)}")
    except Exception as e:
        error(f"❌ Ошибка авторизации: {str(e)}")
        raise Exception(f"Ошибка авторизации: {str(e)}")

def select_account():
    """Выбирает демо- или реальный счет как активный"""
    info(f"🎯 Выбор {'демо' if MODE == 'demo' else 'реального'} счета...")
    headers = get_headers()

    # Шаг 1: Получаем список счетов
    info(f"📋 Получение списка счетов...")
    url = f"{API_BASE}accounts"

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code >= 400:
            error(f"❌ Ошибка получения счетов: HTTP {response.status_code}")
            error(f"   Response: {response.text[:200]}")

        response.raise_for_status()
        accounts_data = response.json()

        if "accounts" not in accounts_data:
            error(f"❌ Неверный формат ответа: {accounts_data}")
            raise ValueError("API не вернул список счетов")

        accounts = accounts_data["accounts"]
        info(f"   Найдено счетов: {len(accounts)}")

    except Exception as e:
        error(f"❌ Ошибка при получении списка счетов: {str(e)}")
        raise Exception(f"Не удалось получить список счетов: {str(e)}")

    # Шаг 2: Ищем нужный тип счета
    target_account = None
    for i, account in enumerate(accounts):
        account_type = account.get("accountType", "UNKNOWN")
        account_id = account.get("accountId", "UNKNOWN")
        info(f"   Счет {i+1}: {account_type} (ID: {account_id})")

        # Логика выбора счета
        if MODE == "demo":
            # В демо-режиме: любой аккаунт (CFD, SPREADBET) с demo URL является демо-счетом
            # Демо НЕ имеет отдельного типа аккаунта - он определяется по URL endpoint'а
            if account_type in ["CFD", "SPREADBET", "DEMO"]:
                target_account = account
                # Проверяем, есть ли demo аккаунт в списке аккаунтов
                info(f"✅ Выбран демо-счет: {account_type} (ID: {account_id})")
                # Берем первый подходящий аккаунт
                break
        else:
            # В реальном режиме ищем реальные счета
            is_real = account_type in ["CFD", "SPREADBET"]
            if is_real:
                target_account = account
                info(f"✅ Выбран реальный счет: {account_type} (ID: {account_id})")
                break

    if not target_account:
        error(f"❌ Не найден {'демо' if MODE == 'demo' else 'реальный'} счет")
        error(f"   Доступные типы счетов: {[acc.get('accountType') for acc in accounts]}")
        raise Exception(f"Не найден {'демо' if MODE == 'demo' else 'реальный'} счет")

    # Шаг 3: Делаем его активным (только если это не уже активный счет)
    # Сначала проверим текущий активный счет
    current_account_id = None
    try:
        # Запрос текущей сессии, чтобы получить текущий accountId
        session_url = f"{API_BASE}session"
        session_response = requests.get(session_url, headers=headers, timeout=10)
        if session_response.status_code == 200:
            session_data = session_response.json()
            current_account_id = session_data.get("currentAccountId")
            info(f"   Текущий активный счет: {current_account_id}")
        else:
            info(f"   Не удалось получить текущий активный счет: HTTP {session_response.status_code}")
    except Exception as e:
        info(f"   Не удалось получить текущий активный счет: {str(e)[:50]}")  # Убираем из логов подробности ошибок

    # Если нужный счет уже активен - пропускаем активацию
    if current_account_id == target_account["accountId"]:
        info(f"   ✅ Счет {target_account['accountId']} уже активен, пропускаем активацию")
    else:
        info(f"🔄 Активация счета {target_account['accountId']}...")
        url = f"{API_BASE}session"
        headers["Version"] = "2"
        payload = {"accountId": target_account["accountId"]}

        try:
            response = requests.put(url, json=payload, headers=headers, timeout=10)

            # Специальная обработка ошибки "аккаунт уже активен" - НЕ ошибка!
            if response.status_code == 400 and "error.not-different.accountId" in response.text:
                info(f"✅ Счет {target_account['accountId']} уже активен (нормальное поведение API)")
                return target_account

            if response.status_code >= 400:
                error(f"❌ Ошибка активации счета: HTTP {response.status_code}")
                error(f"   Response: {response.text[:200]}")

            response.raise_for_status()
            info(f"✅ Активирован {'демо' if MODE == 'demo' else 'реальный'} счет: {target_account['accountId']}")
        except Exception as e:
            error(f"❌ Не удалось активировать счет: {str(e)}")
            raise Exception(f"Ошибка активации счета: {str(e)}")

    return target_account

def get_headers():
    """Готовые заголовки для API-запросов"""
    tokens = get_session_token()
    return {
        "X-SECURITY-TOKEN": tokens["security_token"],
        "CST": tokens["cst"],
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def init_api_session(force=False):
    """Инициализация сессии: логин + выбор счета

    Args:
        force (bool): Принудительно переинициализировать сессию (по умолчанию False)
    """
    global _session_initialized

    # Проверяем, инициализирована ли уже сессия (если не принудительно)
    if _session_initialized and not force:
        return

    info("🚀 Инициализация API сессии...")

    try:
        get_session_token()  # Первичная авторизация
        select_account()     # Выбор демо/реального счета
        _session_initialized = True
        info("✅ API сессия инициализирована успешно")
    except Exception as e:
        # При ошибке сбрасываем флаг
        _session_initialized = False
        error(f"❌ Ошибка инициализации API сессии: {str(e)}")
        raise

def make_request(url, method="get", **kwargs):
    """Универсальный метод для API-запросов с повторными попытками"""
    global _session_initialized

    max_retries = 3
    method_upper = method.upper()

    for attempt in range(max_retries):
        try:
            # Логирование запроса
            if attempt == 0:
                info(f"🌐 API запрос: {method_upper} {url}")

            response = requests.request(method, url, **kwargs)

            # Логирование ответа при ошибке
            if response.status_code >= 400:
                error(f"❌ HTTP {response.status_code} (попытка {attempt + 1}/{max_retries})")
                try:
                    error(f"   Response: {response.text[:300]}")
                except:
                    error("   Response: (не удалось прочитать)")

            # Обработка 401 Unauthorized (сессия устарела)
            if response.status_code == 401 and attempt < max_retries - 1:
                warning(f"   🔄 Сессия устарела, перезапускаем инициализацию... (попытка {attempt + 1})")
                _session_initialized = False  # Сбрасываем флаг для переинициализации
                init_api_session()
                time.sleep(1)
                continue

            response.raise_for_status()
            return response

        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                error(f"❌ Сетевая ошибка после {max_retries} попыток: {str(e)}")
                raise Exception(f"❌ API ошибка после {max_retries} попыток (сеть): {str(e)}")
            warning(f"⚠️ Попытка {attempt + 1} неудачна, повторяем... ({str(e)[:50]})")
        except Exception as e:
            if attempt == max_retries - 1:
                error(f"❌ Общая ошибка после {max_retries} попыток: {str(e)}")
                raise Exception(f"❌ API ошибка после {max_retries} попыток: {str(e)}")
            warning(f"⚠️ Попытка {attempt + 1} неудачна, повторяем... ({str(e)[:50]})")

        time.sleep(2 ** attempt)  # Экспоненциальная задержка

    return None