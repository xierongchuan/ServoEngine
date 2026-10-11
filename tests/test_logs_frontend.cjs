// Регрессии React state/effects без браузера, сети и сторонних test dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('../src/telegram_panel/frontend/node_modules/typescript');

function harness() {
  const hooks = [];
  const effects = [];
  const requests = [];
  const timers = new Map();
    const intervals = new Map();
  let cursor = 0;
  let timerId = 0;
  let tree;
  const location = new URL('https://example.invalid/?token=synthetic-auth&log_symbol=BTCUSDT');
  const window = { location, history: { replaceState(_state, _title, url) { location.href = String(url); } }, addEventListener() {}, removeEventListener() {} };
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[index].value, (next) => { hooks[index].value = typeof next === 'function' ? next(hooks[index].value) : next; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = { current: initial };
      return hooks[index];
    },
    useEffect(callback, dependencies) {
      const index = cursor++;
      const previous = hooks[index];
      if (!previous || dependencies.some((item, i) => item !== previous.dependencies[i])) {
        effects.push(() => { previous?.cleanup?.(); hooks[index] = { dependencies, cleanup: callback() }; });
      }
    },
  };
  const jsx = (type, props) => ({ type, props });
  class LogAPIError extends Error { constructor(status, message) { super(message); this.status = status; } }
  const modules = {
    react,
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../api/logs': {
      LogAPIError,
      getLogSources: async () => ({ categories: [], levels: [], sources: [], exchanges: [], symbols: ['BTCUSDT', 'ETHUSDT'], instances: [], legacy_sources: [], watermark: 0 }),
      getLogEvents(filters, pageCursor, signal) {
        return new Promise((resolve, reject) => requests.push({ filters, cursor: pageCursor, signal, resolve, reject }));
      },
    },
    '../components/LogViewer': { LogViewer: (props) => ({ type: 'viewer', props }) },
  };
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/telegram_panel/frontend/src/pages/Logs.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const callbacks = new Map();
  const subscribe = (type, cb) => { callbacks.set(type, cb); return () => callbacks.delete(type); };
  vm.runInNewContext(code, {
    exports, require(name) { assert.ok(modules[name], `Unexpected module ${name}`); return modules[name]; },
    window, document: { hidden: false, addEventListener() {}, removeEventListener() {} },
    URL, URLSearchParams, AbortController, DOMException, Date, Promise, Map, Set, Error,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
        setInterval(fn) { const id = ++timerId; intervals.set(id, fn); return id; },
        clearInterval(id) { intervals.delete(id); },
  });
  function render() {
    cursor = 0;
    tree = exports.Logs({ subscribe });
    while (effects.length) effects.shift()();
    return tree;
  }
  function nodes(node) {
    if (!node || typeof node !== 'object') return [];
    if (Array.isArray(node)) return node.flatMap((item) => nodes(item));
    if (typeof node.type === 'function') return nodes(node.type(node.props));
    return [node, ...nodes(node.props?.children)];
  }
  function text(node) {
    if (Array.isArray(node)) return node.map(text).join('');
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    return node?.props ? text(node.props.children) : '';
  }
  function button(label) { return nodes(tree).find((node) => node.type === 'button' && text(node).includes(label)); }
  function tick() { const queued = [...timers.values()]; timers.clear(); queued.forEach((fn) => fn()); }
  render();
  return { render, requests, tick, poll: () => { intervals.forEach((fn) => fn()); tick(); }, button, nodes: () => nodes(tree), location, LogAPIError, hooks,
    viewer: () => nodes(tree).find((node) => node.type === 'viewer'),
    stop: () => hooks.forEach((hook) => hook?.cleanup?.()),
  };
}

const event = (id, sequence) => ({ event_id: id, sequence, timestamp: '2026-01-01T00:00:00Z', level: 'INFO', category: 'runtime', message: id });
const page = (events, extra = {}) => ({ events, live_cursor: 'live-1', next_cursor: null, has_more: false, watermark: 1, ...extra });
async function flush(h) { for (let i = 0; i < 8; i++) await Promise.resolve(); h.render(); }

test('переключение фильтра игнорирует поздний ответ старого источника', async () => {
  const h = harness();
  const original = h.requests[0];
  const symbolLabel = h.nodes().find((node) => node.type === 'label' && node.props.children[0] === 'Символ');
  symbolLabel.props.children[1].props.onChange({ target: { value: 'ETHUSDT' } });
  h.render();
  h.nodes().find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
  h.render();
  assert.equal(original.signal.aborted, true);
  const newer = h.requests[1];
  assert.equal(newer.filters.symbol, 'ETHUSDT');
  newer.resolve(page([event('new', 2)]));
  await flush(h);
  original.resolve(page([event('old', 1)]));
  await flush(h);
  assert.deepEqual(Array.from(h.viewer().props.events, (item) => item.event_id), ['new']);
  assert.equal(h.location.searchParams.get('log_symbol'), 'ETHUSDT');
  assert.equal(h.location.searchParams.has('log_token'), false);
  assert.equal(Object.hasOwn(newer.filters, 'token'), false);
  h.stop();
});

test('пауза не двигает live cursor; follow независим; history и live не дублируются', async () => {
  const h = harness();
  h.requests[0].resolve(page([event('one', 1)], { next_cursor: 'before-1' }));
  await flush(h);
  h.button('Пауза live').props.onClick(); h.render();
  h.tick();
  assert.equal(h.requests.length, 1);
  h.button('Следовать за хвостом').props.onClick(); h.render();
  assert.equal(h.viewer().props.follow, false);
  h.button('Продолжить live').props.onClick(); h.render();
  h.tick();
  assert.equal(h.requests[1].cursor, 'live-1');
  h.requests[1].resolve(page([event('one', 1), event('two', 2)], { live_cursor: 'live-2' }));
  await flush(h);
  h.button('Загрузить более старые').props.onClick();
  assert.equal(h.requests[2].cursor, 'before-1');
  h.requests[2].resolve(page([event('zero', 0)], { live_cursor: 'ignored-history-live' }));
  await flush(h);
  assert.deepEqual(Array.from(h.viewer().props.events, (item) => item.event_id), ['zero', 'one', 'two']);
  h.poll();
  assert.equal(h.requests[3].cursor, 'live-2');
  h.stop();
});

function logTransport(client) {
  const source = fs.readFileSync(path.join(__dirname, '../src/telegram_panel/frontend/src/api/logs.ts'), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports, Error, SyntaxError,
    require(name) { assert.equal(name, './client'); return client; },
  });
  return exports;
}

test('журнал использует общий client без своего fetch/credentials и ограничивает фильтры', async () => {
  const calls = [];
  class AuthenticationError extends Error {}
  const api = logTransport({
    AuthenticationError,
    getLogEvents: async (filters, signal) => { calls.push({ filters, signal }); return page([]); },
    getLogSources: async (signal) => { calls.push({ signal }); return { categories: ['runtime'] }; },
  });
  const signal = new AbortController().signal;
  await api.getLogEvents({ symbol: 'BTCUSDT', token: 'must-not-be-query', auth: 'must-not-be-query', limit: 10000 }, 'history', signal);
  assert.equal(calls[0].filters.symbol, 'BTCUSDT');
  assert.equal(Object.hasOwn(calls[0].filters, 'token'), false);
  assert.equal(Object.hasOwn(calls[0].filters, 'auth'), false);
  assert.equal(calls[0].filters.limit, 100);
  assert.equal(calls[0].filters.cursor, 'history');
  assert.equal(calls[0].signal, signal);
  assert.deepEqual(Array.from((await api.getLogSources(signal)).categories), ['runtime']);
  assert.equal(calls[1].signal, signal);
});

test('журнал сохраняет status общего client, скрывает детали ошибок и не оборачивает abort', async () => {
  class AuthenticationError extends Error {}
  let failure;
  const api = logTransport({ AuthenticationError, getLogEvents: async () => { throw failure; } });
  for (const status of [401, 403, 409, 422, 503]) {
    failure = Object.assign(new Error('synthetic-sensitive-detail'), { status });
    await assert.rejects(api.getLogEvents({}), (error) => error instanceof api.LogAPIError && error.status === status && !error.message.includes('sensitive'));
  }
  failure = new AuthenticationError('synthetic-sensitive-detail');
  await assert.rejects(api.getLogEvents({}), (error) => error.status === 401);
  failure = new SyntaxError('synthetic-sensitive-detail');
  await assert.rejects(api.getLogEvents({}), (error) => error.status === 502);
  failure = new DOMException('Отменён', 'AbortError');
  await assert.rejects(api.getLogEvents({}), (error) => error === failure);
});

test('ошибка авторизации видима и прекращает повторный live polling', async () => {
  const h = harness();
  h.requests[0].resolve(page([event('one', 1)]));
  await flush(h);
  h.tick();
  h.requests[1].reject(new h.LogAPIError(401, 'Сессия истекла'));
  await flush(h);
  assert.ok(h.nodes().some((node) => node.props.role === 'alert' && node.props.children === 'Сессия истекла'));
  h.poll();
  assert.equal(h.requests.length, 2);
  h.stop();
});
