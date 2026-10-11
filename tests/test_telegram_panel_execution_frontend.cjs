// Карточка журнала: polling, обновление terminal proof и отмена без реального transport.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'src/telegram_panel/frontend/node_modules/typescript'));

function harness() {
  const hooks = [];
  const effects = [];
  const requests = [];
  const intervals = new Map();
  const timers = new Map();
  let cursor = 0;
  let id = 0;
  let tree;
  const react = {
    useState(initial) {
      const slot = cursor++;
      hooks[slot] ??= { value: initial };
      return [hooks[slot].value, next => { hooks[slot].value = typeof next === 'function' ? next(hooks[slot].value) : next; }];
    },
    useCallback(callback, deps) {
      const slot = cursor++;
      hooks[slot] ??= { callback, deps };
      return hooks[slot].callback;
    },
    useEffect(callback, deps) {
      const slot = cursor++;
      const previous = hooks[slot];
      if (!previous || deps.some((dep, index) => dep !== previous.deps[index])) {
        effects.push(() => { previous?.cleanup?.(); hooks[slot] = { deps, cleanup: callback() }; });
      }
    },
  };
  const jsx = (type, props) => ({ type, props });
  const exports = {};
  const modules = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    '../api/client': {
      AuthenticationError: class extends Error {},
      getDashboard: async () => ({ symbols: [], strategy: 'MACDX' }),
      getActiveTrades: async () => [], getTradeStats: async () => ({}),
      getExecutionState: signal => new Promise((resolve, reject) => requests.push({ signal, resolve, reject })),
    },
  };
  for (const name of ['StatsCard', 'TradeCard', 'Spinner', 'Card', 'StatusDot']) {
    const directory = ['Card', 'StatusDot'].includes(name) ? 'ui/' : '';
    modules[`../components/${directory}${name}`] = { [name]: props => jsx(name, props) };
  }
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'src/telegram_panel/frontend/src/pages/Dashboard.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  vm.runInNewContext(code, { exports, require: name => { assert.ok(modules[name], name); return modules[name]; },
    console, AbortController, Date, Set, document: { hidden: false, addEventListener() {}, removeEventListener() {} },
    setTimeout: callback => { const key = ++id; timers.set(key, callback); return key; },
    clearTimeout: key => timers.delete(key),
    setInterval: callback => { const key = ++id; intervals.set(key, callback); return key; },
    clearInterval: key => intervals.delete(key),
  });
  const subscribe = () => () => {};
  const render = () => {
    cursor = 0;
    tree = exports.Dashboard({ subscribe });
    while (effects.length) effects.shift()();
  };
  const text = node => Array.isArray(node) ? node.map(text).join('') : typeof node === 'string' || typeof node === 'number'
    ? String(node) : node?.props ? text(node.props.children) : '';
  render();
  return { requests, render, text: () => text(tree), poll: () => intervals.forEach(callback => callback()),
    stop: () => hooks.forEach(hook => hook?.cleanup?.()), intervals, timers };
}

const row = { source: 'execution', account: 'account', owner: 'owner', symbol: 'BTCUSDT',
  operation: 'entry', state: 'UNKNOWN', order_id: 'order-unknown', quantity: '.01', updated: 123, reason: null };
function snapshot(pending = [row], blocks = []) {
  return { status: pending.length || blocks.length ? 'attention' : 'ok', pending, blocks,
    counts: { pending: pending.length, blocks: blocks.length, ownership: 0 } };
}
async function flush(h) { for (let i = 0; i < 8; i++) await Promise.resolve(); h.render(); }

test('Card показывает pending и обновляет terminal proof через polling без снятия паузы', async () => {
  const h = harness();
  const block = { ...row, operation: 'pause', state: 'PAUSED', order_id: null, reason: 'Проверка оператором' };
  h.requests[0].resolve(snapshot([row], [block]));
  await flush(h);
  assert.ok(h.text().includes('Ожидающие команды: 1 · Блокировки: 1'));
  assert.ok(h.text().includes('order-unknown'));
  h.poll();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(snapshot([], [block]));
  await flush(h);
  assert.ok(h.text().includes('Ожидающие команды: 0 · Блокировки: 1'));
  assert.ok(!h.text().includes('order-unknown'));
  assert.ok(h.text().includes('Проверка оператором'));
  assert.ok(h.text().includes('здесь не снимаются'));
  h.stop();
  assert.equal(h.intervals.size, 0);
  assert.equal(h.timers.size, 0);
});

test('Polling не перекрывает запрос; unmount отменяет сигнал и игнорирует поздний ответ', async () => {
  const h = harness();
  h.poll(); h.poll();
  assert.equal(h.requests.length, 1);
  await flush(h);
  h.stop();
  assert.equal(h.requests[0].signal.aborted, true);
  h.requests[0].resolve(snapshot());
  await flush(h);
  assert.ok(!h.text().includes('Ожидающие команды: 1'));
});
