// Регрессии сохранения конфигурации и ожидания графиков без сети и браузерных зависимостей.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.resolve(__dirname, '../src/telegram_panel/frontend');
const ts = require(frontend + '/node_modules/typescript');

function load(relative, modules = {}, globals = {}, suffix = '') {
  const exports = {};
  const source = fs.readFileSync(`${frontend}/src/${relative}`, 'utf8') + suffix;
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  vm.runInNewContext(code, { exports, Error, console, AbortController,
    require: name => { assert.ok(name in modules, `Неожиданная зависимость ${name}`); return modules[name]; },
    ...globals }, { filename: relative });
  return exports;
}
const running = { state: 'running', runtime_pid: 123, execution: {}, control_enabled: true, stale: false };
const stopped = state => ({ ...running, state, runtime_pid: null, execution: null });

function actions(readStatus = async () => running) {
  return load('components/config/runtimeActions.ts', { '../../api/client': { getRuntimeStatus: readStatus } });
}

test('Команды worker разрешены только для свежего running с PID и snapshot', () => {
  const { canControlWorker } = actions();
  assert.equal(canControlWorker(running), true);
  for (const status of [null, stopped('stopped'), stopped('crashed'), { ...running, stale: true },
    { ...running, control_enabled: false }, { ...running, runtime_pid: 0 },
    { ...running, runtime_pid: '123' }, { ...running, execution: [] },
    { ...running, execution: null }, { ...running, state: 'starting' }]) {
    assert.equal(canControlWorker(status), false);
  }
});

for (const state of ['stopped', 'crashed']) {
  test(`При ${state} конфиг сохраняется, UI обновляется, команда worker не отправляется`, async () => {
    const calls = [];
    const notice = await actions(async () => { calls.push('status'); return stopped(state); }).applyRuntimeConfigChange({
      save: async () => { calls.push('save'); return { id: 'saved' }; },
      refresh: async () => { calls.push('refresh'); },
      queue: async () => { calls.push('queue'); },
    });
    assert.deepEqual(calls, ['save', 'refresh', 'status']);
    assert.equal(notice.type, 'success');
    assert.match(notice.text, /общем «Старт»/);
  });
}

test('Успех save + отказ queue остаётся частичным успехом, UI обновляется раньше применения', async () => {
  const calls = [];
  const saved = { enabled: true };
  const notice = await actions(async () => { calls.push('status'); return running; }).applyRuntimeConfigChange({
    save: async () => { calls.push('save'); return saved; },
    refresh: async () => { calls.push('refresh'); },
    queue: async value => { assert.equal(value, saved); calls.push('queue'); throw new Error('conflict'); },
  });
  assert.deepEqual(calls, ['save', 'refresh', 'status', 'queue']);
  assert.equal(notice.type, 'warning');
  assert.match(notice.text, /сохранение повторять не нужно/);
});

test('Running отправляет одну команду; ошибка save не вызывает refresh/status/queue', async () => {
  let count = 0;
  const notice = await actions().applyRuntimeConfigChange({ save: async () => 42, refresh: () => {},
    queue: async saved => { assert.equal(saved, 42); count++; } });
  assert.equal(count, 1);
  assert.equal(notice.type, 'success');
  const forbidden = () => assert.fail('После отказа save дальнейшие действия запрещены');
  await assert.rejects(actions(forbidden).applyRuntimeConfigChange({
    save: async () => { throw new Error('save failed'); }, refresh: forbidden, queue: forbidden,
  }), /save failed/);
});

test('Stale, переходный и недоступный runtime не принимают команды после save', async () => {
  for (const readStatus of [async () => ({ ...running, stale: true }), async () => ({ ...running, state: 'starting' }),
    async () => { throw new Error('unavailable'); }]) {
    let refreshed = 0;
    const notice = await actions(readStatus).applyRuntimeConfigChange({ save: async () => {},
      refresh: () => { refreshed++; }, queue: () => assert.fail('Нельзя отправлять команду') });
    assert.equal(refreshed, 1);
    assert.equal(notice.type, 'warning');
    assert.match(notice.text, /сохранены/);
  }
});

test('Отказ refresh после save предупреждает и не отправляет worker-команду', async () => {
  const notice = await actions(() => assert.fail('Нет актуального UI')).applyRuntimeConfigChange({
    save: async () => {}, refresh: () => { throw new Error('refresh failed'); },
    queue: () => assert.fail('Нет актуального UI'),
  });
  assert.equal(notice.type, 'warning');
  assert.match(notice.text, /Настройки сохранены/);
});

const jsx = (type, props) => ({ type, props });
function nodes(node) {
  if (!node || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap(nodes);
  return [node, ...nodes(node.props?.children)];
}
function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return node?.props ? text(node.props.children) : '';
}
function hooksHarness() {
  const hooks = [], effects = [], intervals = new Set();
  let cursor = 0, tree, component;
  const react = {
    useCallback: callback => callback,
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[index].value, value => { hooks[index].value = typeof value === 'function' ? value(hooks[index].value) : value; }];
    },
    useRef(initial) { const index = cursor++; return hooks[index] ??= { current: initial }; },
    useEffect(callback, deps) {
      const index = cursor++, previous = hooks[index];
      if (!previous || deps.some((dep, i) => dep !== previous.deps[i])) {
        effects.push(() => { previous?.cleanup?.(); hooks[index] = { deps, cleanup: callback() }; });
      }
    },
  };
  function render() { cursor = 0; tree = component(); while (effects.length) effects.shift()(); return tree; }
  const timers = { setTimeout: () => 1, clearTimeout() {},
    setInterval: callback => { intervals.add(callback); return callback; }, clearInterval: callback => intervals.delete(callback) };
  return { react, timers, intervals, render, mount: callback => { component = callback; render(); },
    nodes: () => nodes(tree), text: () => text(tree),
    stop: () => hooks.forEach(hook => hook?.cleanup?.()) };
}
async function flush(h) { for (let i = 0; i < 16; i++) await Promise.resolve(); h.render(); }

function runtimeTabHarness(status, queueError = false) {
  const h = hooksHarness(), calls = [], notices = [];
  const instance = { id: 'btc_macdx', symbol: 'BTCUSDT', strategy: 'MACDX', profile: 'default', enabled: false };
  const props = { symbolProfiles: { symbols: ['BTCUSDT'], disabled_symbols: [], strategy_instances: [instance] },
    activeTrades: [], profiles: { available: ['default'], profiles: {} }, strategies: { available: ['MACDX'] },
    onRefresh: async () => { calls.push('refresh'); props.symbolProfiles = { ...props.symbolProfiles, strategy_instances: [{ ...instance, enabled: true }] }; },
    onSuccess: message => notices.push({ type: 'success', message }), onWarning: message => notices.push({ type: 'warning', message }),
    onError: message => notices.push({ type: 'error', message }) };
  const client = { getRuntimeStatus: async () => status, updateStrategyInstance: async (_, data) => { calls.push('save'); return data; },
    startSymbolRuntime: async () => { calls.push('queue'); if (queueError) throw new Error('worker conflict'); },
    stopRuntime: async () => { calls.push('stop-runtime'); } };
  const modules = { react: h.react, 'react/jsx-runtime': { jsx, jsxs: jsx }, '../api/client': client,
    '../components/config/runtimeActions': actions(client.getRuntimeStatus) };
  for (const name of ['ProfileCard', 'ProfileEditor', 'config/ExecutionSettings', 'config/AISettings', 'Spinner',
    'ui/Button', 'ui/Card', 'ui/List', 'ui/StatusDot', 'ui/Tabs']) {
    modules[`../components/${name}`] = { Button: 'Button', Card: 'Section' };
  }
  const component = load('pages/Settings.tsx', modules, { window: h.timers, confirm: () => true }, '\nexport { RuntimeTab };');
  h.mount(() => component.RuntimeTab(props));
  return { ...h, calls, notices, button: label => h.nodes().find(node => text(node) === label && ['button', 'Button'].includes(node.type)) };
}

for (const status of [stopped('stopped'), stopped('crashed'), running]) {
  test(`Карточка instance сразу обновляется при ${status.state}, даже при отказе применения`, async () => {
    const h = runtimeTabHarness(status, true);
    await flush(h);
    await h.button('Выключен').props.onClick();
    await flush(h);
    assert.ok(h.button('Включён'));
    assert.ok(!h.button('Выключен'));
    assert.deepEqual(h.calls, status.state === 'running' ? ['save', 'refresh', 'queue'] : ['save', 'refresh']);
    assert.equal(h.notices[0].type, status.state === 'running' ? 'warning' : 'success');
    if (status.state !== 'running') {
      assert.match(h.text(), /Ожидает запуска runtime/);
      assert.equal(h.button('restart').props.disabled, true);
    }
    if (status.state === 'crashed') {
      assert.equal(h.button('Стоп').props.disabled, false);
      await h.button('Стоп').props.onClick();
      assert.ok(h.calls.includes('stop-runtime'));
    }
    h.stop();
  });
}

test('App сохраняет страницу Charts между вкладками и передаёт active=false скрытой странице', async () => {
  const h = hooksHarness();
  const subscribe = () => () => {};
  const modules = { react: h.react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    './api/client': { getDashboard: async () => ({}) },
    './hooks/useWebSocket': { useWebSocket: () => ({ subscribe, isConnected: true }) },
    './hooks/useTelegram': { useTelegram() {} },
  };
  for (const name of ['Dashboard', 'Charts', 'Trades', 'Logs', 'Settings', 'Journal']) modules[`./pages/${name}`] = { [name]: name };
  for (const name of ['TabBar', 'Spinner']) modules[`./components/${name}`] = { [name]: name };
  const component = load('App.tsx', modules, { window: { location: { hash: '', search: '' } }, URLSearchParams });
  h.mount(() => component.App()); await flush(h);
  const change = tab => { h.nodes().find(node => node.type === 'TabBar').props.onTabChange(tab); h.render(); };
  assert.ok(!h.nodes().some(node => node.type === 'Charts'));
  change('charts');
  assert.equal(h.nodes().filter(node => node.type === 'Charts').length, 1);
  assert.equal(h.nodes().find(node => node.type === 'Charts').props.active, true);
  change('logs');
  assert.equal(h.nodes().find(node => node.type === 'Charts').props.active, false);
  assert.ok(h.nodes().some(node => node.type === 'div' && node.props.hidden === true));
  change('charts');
  assert.equal(h.nodes().filter(node => node.type === 'Charts').length, 1);
  assert.equal(h.nodes().find(node => node.type === 'Charts').props.active, true);
  h.stop();
});

function chartsHarness(error, initiallyAvailable = false) {
  const h = hooksHarness(), calls = [];
  class APIError extends Error { constructor(status, message) { super(message); this.status = status; } }
  class AuthenticationError extends APIError {}
  let available = initiallyAvailable, active = true, hold = false, failure = null;
  const pending = [];
  const events = new Map();
  const stream = { exchange: 'bingx', market_type: 'perpetual', symbol: 'BTCUSDT', interval: '5m' };
  const snapshot = { symbol: 'BTCUSDT', available_sources: [stream], candles: [], source: { stream }, range: 'DAY', interval: '5m' };
  const modules = { react: h.react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    '../api/client': { APIError, AuthenticationError,
      getDashboard: async () => ({ symbols: ['BTCUSDT'], active_trades: [] }),
      getChartSources: async () => { calls.push('sources'); return available ? [stream] : []; },
      getChartData: async (_, __, signal, source) => {
        calls.push('data');
        if (hold) return new Promise((resolve, reject) => pending.push({ resolve, reject, signal }));
        if (failure) throw failure;
        if (!available) throw error === 'auth'
          ? new AuthenticationError(401, 'Сессия истекла') : new APIError(error, 'API error');
        assert.equal(source, stream); return snapshot;
      },
    }, '../api/chartTypes': { chartStreamKey: source => JSON.stringify(source) } };
  for (const name of ['InteractiveChart', 'Spinner', 'ui/Tabs', 'ui/StatusDot']) modules[`../components/${name}`] = {};
  let now = 10000;
  const component = load('pages/Charts.tsx', modules, { ...h.timers,
    Date: class extends Date { static now() { return now; } },
    document: { hidden: false, addEventListener() {}, removeEventListener() {} } });
  const subscribe = (kind, callback) => { events.set(kind, callback); return () => events.delete(kind); };
  h.mount(() => component.Charts({ subscribe, active }));
  const poll = () => { now += 16000; for (const callback of h.intervals) callback(); };
  return { ...h, calls, events, pending, snapshot, poll,
    ready: () => { available = true; poll(); },
    show: visible => { active = visible; now += 16000; h.render(); },
    holdNext: () => { hold = true; },
    rejectAuth: () => { failure = new AuthenticationError(401, 'Сессия истекла'); poll(); },
  };
}

test('404 графика — доступное ожидание данных; polling обнаруживает новый source и снимает статус', async () => {
  const h = chartsHarness(404);
  await flush(h); await flush(h);
  assert.match(h.text(), /Данные графика ещё не появились/);
  assert.match(h.text(), /read_only тоже собирает данные/);
  assert.ok(h.nodes().some(node => node.props?.role === 'status' && text(node).includes('Данные графика')));
  assert.ok(!h.nodes().some(node => node.props?.role === 'alert'));
  h.ready(); await flush(h);
  assert.deepEqual(h.calls, ['sources', 'data', 'sources', 'data']);
  assert.ok(!h.text().includes('Данные графика ещё не появились'));
  h.stop();
});

test('Возвращение во вкладку показывает прежний снимок сразу и обновляет в фоне без повторного sources', async () => {
  const h = chartsHarness(404, true);
  await flush(h); await flush(h);
  assert.ok(h.nodes().some(node => node.props?.data === h.snapshot));
  h.show(false);
  assert.equal(h.intervals.size, 0);
  assert.equal(h.events.size, 0);
  const before = h.calls.length;
  h.poll(); await flush(h);
  assert.equal(h.calls.length, before);
  h.holdNext();
  h.show(true); await flush(h);
  assert.ok(h.nodes().some(node => node.props?.data === h.snapshot));
  assert.match(h.text(), /Обновляем график/);
  assert.ok(!h.nodes().some(node => node.props?.['aria-label'] === 'Загрузка графика'));
  assert.deepEqual(h.calls, ['sources', 'data', 'data']);
  h.pending[0].resolve(h.snapshot); await flush(h);
  assert.ok(!h.text().includes('Обновляем график'));
  h.stop();
});

test('Уход со вкладки отменяет загрузку; поздний ответ не заменяет сохранённый снимок', async () => {
  const h = chartsHarness(404, true);
  await flush(h); await flush(h);
  h.holdNext(); h.poll(); await flush(h);
  h.show(false);
  assert.equal(h.pending[0].signal.aborted, true);
  h.pending[0].resolve({ ...h.snapshot, symbol: 'LATE' }); await flush(h);
  assert.ok(h.nodes().some(node => node.props?.data === h.snapshot));
  assert.ok(!h.nodes().some(node => node.props?.data?.symbol === 'LATE'));
  h.stop();
});

test('Истечение авторизации удаляет предыдущий снимок и прекращает polling запросов', async () => {
  const h = chartsHarness(404, true);
  await flush(h); await flush(h);
  h.rejectAuth(); await flush(h);
  assert.ok(!h.nodes().some(node => node.props?.data === h.snapshot));
  assert.match(h.text(), /Сессия истекла/);
  const before = h.calls.length;
  h.poll(); await flush(h);
  assert.equal(h.calls.length, before);
  h.stop();
});

test('Таймаут графика получает адресное сообщение и сохраняет предыдущий снимок', async () => {
  const h = chartsHarness(404, true);
  await flush(h); await flush(h);
  h.holdNext(); h.poll(); await flush(h);
  h.pending[0].reject(Object.assign(new Error('cancelled'), { name: 'AbortError' })); await flush(h);
  assert.match(h.text(), /больше 10 секунд/);
  assert.ok(!h.text().includes('Некорректный ответ сервера'));
  assert.ok(h.nodes().some(node => node.props?.data === h.snapshot));
  h.stop();
});

for (const error of [503, 'auth']) {
  test(`Ошибка ${error} не маскируется ожиданием данных`, async () => {
    const h = chartsHarness(error);
    await flush(h); await flush(h);
    assert.ok(h.nodes().some(node => node.props?.role === 'alert'));
    assert.ok(!h.text().includes('Данные графика ещё не появились'));
    h.stop();
  });
}
