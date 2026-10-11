"""Запрет входов GRID без гарантированного lifecycle биржевой защиты."""


# Release gate, не настройка пользователя: активная пара SL/TP на VST ещё не подтверждена.
BINGX_NATIVE_MARKET_VERIFIED = False

PERPETUAL_ENTRY_BLOCK_REASON = (
    "Новые входы perpetual GRID запрещены: initial LIMIT fills не устанавливают "
    "биржевой SL/TP, а безопасный lifecycle защиты при изменении объёма не реализован. "
    "Локальный emergency stop не гарантирует биржевую защиту. "
    "Доступны сверка, отмена собственных заявок и уменьшение собственного объёма."
)


def grid_protection_capabilities(settings):
    """Проверенный контракт API, а не opt-in или обещание клиента о поддержке."""
    exchange = settings["exchange"]
    perpetual = settings["market_type"] == "perpetual"
    gaps = {
        "bingx": [
            "Документация не гарантирует атомарную активацию SL/TP на каждом partial fill",
            "Parent ID li описан только в WS; durable REST recovery связи не доказан",
            "Атомарный resize обеих защит при нескольких уровнях не доказан",
            "Сохранение защиты partial fill при cancel entry не доказано",
        ],
        "mexc": [
            "Документация не гарантирует атомарную активацию SL/TP на каждом partial fill",
            "Атомарный resize обеих защит при нескольких уровнях не доказан",
            "Доступность актуального order/create не доказывается старым maintenance API",
            "Сохранение защиты partial fill при cancel entry не доказано",
            "Полная актуальная схема reference/volume stoporder readback не подтверждена",
        ],
    }
    return {
        "exchange": exchange,
        "market_type": settings["market_type"],
        "entry_allowed": False,
        "native_market_entry_allowed": not native_grid_entry_block_reason(settings),
        "native_market_parallelism": 1 if not native_grid_entry_block_reason(settings) else 0,
        "native_parent_link": "WS li + durable context" if exchange == "bingx" and perpetual else None,
        "attached_entry_fields": perpetual and exchange in {"bingx", "mexc"},
        "atomic_partial_protection": False,
        "max_safe_entry_parallelism": 0,
        "read_only_parent_link": "orderId" if perpetual and exchange == "mexc" else None,
        "snapshot_reconciliation_only": perpetual,
        "atomic_resize": False,
        "entry_cancel_preserves_partial_protection": False,
        "unresolved": list(gaps.get(exchange, ["Контракт биржевой защиты не исследован"])),
    }


def native_grid_entry_block_reason(settings):
    if settings["exchange"] != "bingx" or settings["market_type"] != "perpetual":
        return "GRID native MARKET: текущий рабочий контракт поддерживает только BingX perpetual"
    if settings["execution_mode"] == "read_only":
        return "GRID native MARKET: исполнение запрещено режимом read_only"
    if not BINGX_NATIVE_MARKET_VERIFIED:
        return "GRID native MARKET: активная биржевая пара SL/TP и полный lifecycle на VST ещё не подтверждены; новые входы отключены"
    return ""


def grid_entry_block_reason(settings):
    # Ни live/demo, ни локальная защита Spot не разрешают незащищённый perpetual-вход.
    return PERPETUAL_ENTRY_BLOCK_REASON if settings["market_type"] == "perpetual" else ""
