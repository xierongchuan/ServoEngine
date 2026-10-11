"""Клиенты данных и исполнения: отдельные owners, общий durable журнал аккаунта."""

import json
import os
from pathlib import Path

from src.trading_settings import execution_account_label, get_trading_settings, validate_trading_settings
from src.utils.logger import error

_client_instances = {}
_client_instance = None
_client_context = {}


def configure_client_context(*, owner_id=None, account_id=None, data_dir=None, runtime_config=None):
    """Вызывается в дочернем процессе до создания клиентов, не меняет аккаунт биржи."""
    global _client_context, _client_instance
    _client_context = dict(owner_id=owner_id, account_id=account_id, data_dir=data_dir,
                           runtime_config=runtime_config or {})
    _client_instance = None


def _selected(exchange=None, market_type=None, settings=None):
    selected = validate_trading_settings(settings) if settings is not None else get_trading_settings()
    if settings is not None and ((exchange is not None and exchange.lower() != selected['exchange']) or
                                (market_type is not None and market_type.lower() != selected['market_type'])):
        raise ValueError('Параметры фабрики не совпадают с execution')
    if exchange is not None:
        if settings is None and str(exchange).lower() != selected['exchange']:
            selected['execution_mode'] = 'read_only'
        selected['exchange'] = str(exchange).lower()
    if market_type is not None:
        if settings is None and str(market_type).lower() != selected['market_type']:
            selected['execution_mode'] = 'read_only'
        selected['market_type'] = str(market_type).lower()
    if selected['market_type'] == 'spot' and settings is None:
        selected.update(position_mode='one_way', grid_mode='inventory')
        selected['protection']['trigger_price'] = 'last'
    return validate_trading_settings(selected)


def _selection(exchange=None, market_type=None, settings=None):
    selected = _selected(exchange, market_type, settings)
    return selected['exchange'], selected['market_type'], selected['execution_mode'] == 'demo'


def _create_client(exchange, market_type, is_demo, use_new_impl=True, settings=None):
    from .config.base import ConfigFactory
    config = ConfigFactory.create(exchange, is_demo=is_demo,
                                  market_type=market_type if exchange == 'mexc' else None, settings=settings)
    if exchange == 'bingx':
        from .impl.bingx_client import BingXClient
        return BingXClient(config)
    if exchange == 'mexc' and market_type == 'spot':
        from .impl.mexc_spot_client import MEXCSpotClient
        return MEXCSpotClient(config)
    if exchange == 'mexc' and market_type == 'perpetual':
        from .impl.mexc_futures_client import MEXCFuturesClient
        return MEXCFuturesClient(config)
    raise ValueError(f'Неподдерживаемый источник: {exchange}/{market_type}')


class _ReadOnlyClient:
    """Дополнительный guard для совместимых внешних вызовов."""

    def __init__(self, client):
        self.__client = client

    def check_prerequisites(self):
        error('❌ Автоторговля запрещена: execution_mode=read_only')
        return False

    def __getattr__(self, name):
        if name.startswith(('place_', 'submit_', 'close_', 'cancel_', 'set_', 'modify_', 'update_', '_')):
            def forbidden(*args, **kwargs):
                raise PermissionError('Режим read_only запрещает торговые операции')
            return forbidden
        return getattr(self.__client, name)


def _identity(selected, owner_id=None, account_id=None, runtime_config=None):
    runtime = runtime_config or _client_context.get('runtime_config')
    if not runtime:
        from src.config import BOT_CONFIG
        runtime = BOT_CONFIG
    owner = str(owner_id or _client_context.get('owner_id') or runtime.get('STRATEGY_INSTANCE_ID') or
                runtime.get('STRATEGY_INSTANCE', {}).get('id') or 'default')
    account = str(account_id or _client_context.get('account_id') or execution_account_label(selected))
    root = Path(_client_context.get('data_dir') or runtime.get('DATA_DIR') or os.getenv('SERVO_DATA_DIR', 'data'))
    return owner, account, root


def get_market_data_client(exchange=None, market_type=None, settings=None):
    """Публичные readers не создают coordinator, Spot ledger или private запросы."""
    selected = _selected(exchange, market_type, settings)
    selected['execution_mode'] = 'read_only'
    key = ('market', os.getpid(), json.dumps(selected, sort_keys=True))
    if key not in _client_instances:
        _client_instances[key] = _ReadOnlyClient(_create_client(
            selected['exchange'], selected['market_type'], False, settings=selected))
    return _client_instances[key]


def get_trading_client(exchange=None, market_type=None, use_new_impl=True, settings=None, *,
                       owner_id=None, account_id=None, runtime_config=None):
    selected = _selected(exchange, market_type, settings)
    owner, account, root = _identity(selected, owner_id, account_id, runtime_config)
    key = ('trade', os.getpid(), owner, account, str(root.resolve()), json.dumps(selected, sort_keys=True))
    if key not in _client_instances:
        raw = _create_client(selected['exchange'], selected['market_type'],
                             selected['execution_mode'] == 'demo', use_new_impl, settings=selected)
        if selected['execution_mode'] == 'read_only':
            client = _ReadOnlyClient(raw)
        elif selected['market_type'] == 'spot':
            from .impl.spot_trading_adapter import SpotTradingAdapter
            from .ws_provider_factory import get_market_snapshot
            client = SpotTradingAdapter(raw, ledger_path=root / 'spot_ledger.sqlite3',
                account_id=account, owner_id=owner, settings=selected, spot_options=selected['spot'],
                data_dir=str(root), snapshot_reader=get_market_snapshot,
                tick_max_age=selected['market_data']['max_age_seconds'])
            client.start_supervisor()
        else:
            from src.core.execution_coordinator import attach_execution_safety
            client = attach_execution_safety(raw, journal_path=root / 'execution.sqlite3',
                                             settings=selected, owner_id=owner, account_id=account)
        _client_instances[key] = client
    return _client_instances[key]


def get_exchange_client(use_new_impl=True, settings=None, *, owner_id=None, account_id=None, runtime_config=None):
    global _client_instance
    client = get_trading_client(use_new_impl=use_new_impl, settings=settings,
                               owner_id=owner_id, account_id=account_id, runtime_config=runtime_config)
    _client_instance = client
    return client


def reset_client():
    global _client_instance, _client_context
    from .impl.spot_trading_adapter import SpotTradingAdapter
    seen = set()
    for client in _client_instances.values():
        if isinstance(client, SpotTradingAdapter) and id(client) not in seen:
            client.shutdown()
            seen.add(id(client))
    _client_instances.clear()
    _client_instance = None
    _client_context = {}


__all__ = ['get_exchange_client', 'get_market_data_client', 'get_trading_client',
           'reset_client', 'configure_client_context']
