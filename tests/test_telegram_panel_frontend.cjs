// Изолированные регрессии frontend: без браузерных секретов и внешних запросов.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const frontendRoot = require('node:path').resolve(__dirname, '../src/telegram_panel/frontend');
const ts = require(frontendRoot + '/node_modules/typescript');

function load(path, modules, globals = {}) {
  const source = fs.readFileSync(`${frontendRoot}/src/${path}`, 'utf8').replaceAll('import.meta.env', '({})');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: name => {
    assert.ok(name in modules, `Неожиданная зависимость: ${name}`);
    return modules[name];
  }, URL, URLSearchParams, AbortController, console, ...globals }, { filename: path });
  return exports;
}

test('HTTP и WS используют headers, сохраняют AbortSignal и не выводят секреты', async () => {
  const calls = [];
  const debug = [];
  const window = { location: { origin: 'https://panel.example', hash: '#/auth?token=secret-test-token', search: '' } };
  const client = load('api/client.ts', {}, {
    window, console: { log: (...args) => debug.push(args) },
    fetch: async (url, options) => { calls.push({ url, options }); return { ok: true,
      json: async () => url.includes('ws-ticket') ? { ticket: 'one-use-ticket' } : {} }; },
  });
  const controller = new AbortController();
  await client.getChartData('BTC-USDT', 'AUTO', controller.signal, {
    exchange: 'mexc', market_type: 'perpetual', symbol: 'BTCUSDT', interval: '5m',
  });
  assert.equal(calls[0].options.headers['X-Web-Token'], 'secret-test-token');
  assert.equal(calls[0].options.signal, controller.signal);
  assert.equal(calls[0].options.cache, 'no-store');
  assert.ok(calls[0].url.includes('interval=5m'));
  assert.ok(!calls[0].url.includes('secret-test-token'));
  const wsUrl = await client.getWebSocketUrl(controller.signal);
  assert.equal(wsUrl, 'wss://panel.example/ws?ticket=one-use-ticket');
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(debug.length, 0);
});

test('Просроченная Telegram-сессия получает понятное сообщение и отдельный тип ошибки', async () => {
  const client = load('api/client.ts', {}, {
    window: { location: { origin: 'https://panel.example', hash: '', search: '' },
      Telegram: { WebApp: { initData: 'signed-test-data' } } },
    fetch: async (_, options) => {
      assert.equal(options.headers['X-Telegram-Init-Data'], 'signed-test-data');
      return { ok: false, status: 401 };
    },
  });
  await assert.rejects(client.getDashboard(), error => error instanceof client.AuthenticationError
    && error.message.includes('откройте её заново'));
});

test('Общий fetchAPI экспортирован; ошибки сохраняют status и AuthenticationError совместим', async () => {
  const window = { location: { origin: 'https://panel.example', hash: '', search: '' } };
  for (const status of [401, 403, 409, 422, 503]) {
    const client = load('api/client.ts', {}, { window,
      fetch: async () => ({ ok: false, status, json: async () => ({ detail: 'Безопасная ошибка', request_id: 'request-1' }) }),
    });
    assert.equal(typeof client.fetchAPI, 'function');
    await assert.rejects(client.fetchAPI('/api/execution/state'), error => error instanceof client.APIError
      && error.status === status && (status > 403 || error instanceof client.AuthenticationError));
    assert.equal(new client.AuthenticationError('expired').status, 401);
  }
  const client = load('api/client.ts', {}, { window,
    fetch: async () => ({ ok: true, json: async () => { throw new SyntaxError('raw-secret'); } }),
  });
  await assert.rejects(client.fetchAPI('/api/execution/state'), error => error instanceof client.APIError
    && error.status === 502 && !error.message.includes('raw-secret'));
});

test('WS cleanup отменяет билет, удаляет таймеры и не создаёт соединение после unmount', async () => {
  let effect;
  let resolveTicket;
  let signal;
  let sockets = 0;
  const timers = new Set();
  const module = load('hooks/useWebSocket.ts', {
    react: {
      useState: initial => [initial, () => {}], useRef: initial => ({ current: initial }),
      useCallback: callback => callback, useEffect: callback => { effect = callback; },
    },
    '../api/client': {
      AuthenticationError: class extends Error {},
      getWebSocketUrl: incoming => { signal = incoming; return new Promise(resolve => { resolveTicket = resolve; }); },
    },
  }, {
    WebSocket: class { constructor() { sockets++; } },
    setTimeout: callback => { timers.add(callback); return callback; },
    clearTimeout: callback => timers.delete(callback),
  });
  module.useWebSocket();
  const cleanup = effect();
  assert.equal(timers.size, 1);
  cleanup();
  assert.equal(signal.aborted, true);
  resolveTicket('wss://panel.example/ws?ticket=fake');
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(sockets, 0);
  assert.equal(timers.size, 0);
});

function chartHarness() {
  const refs = [];
  const effects = [];
  let index = 0;
  let pending = [];
  const container = { clientWidth: 640, clientHeight: 600 };
  const react = {
    useRef(initial) {
      const slot = index++;
      refs[slot] ??= { current: slot === 0 ? container : initial };
      return refs[slot];
    },
    useEffect(callback, deps) {
      const slot = index++;
      if (!effects[slot] || deps.some((dep, i) => dep !== effects[slot].deps[i])) {
        pending.push(() => {
          effects[slot]?.cleanup?.();
          effects[slot] = { deps, cleanup: callback() };
        });
      }
    },
  };
  const records = [];
  let visible = { from: 1, to: 2 };
  let fitCount = 0;
  let removed = 0;
  let followed = 0;
  const timeScale = {
    getVisibleRange: () => visible,
    setVisibleRange: range => { visible = range; },
    fitContent: () => { fitCount++; },
    scrollToRealTime: () => { followed++; },
  };
  const add = (kind, options = {}) => {
    const record = { kind, options, data: [], lines: [], removedLines: [] };
    records.push(record);
    return {
      setData: data => { record.data = data; },
      createPriceLine: line => { record.lines.push(line); return line; },
      removePriceLine: line => record.removedLines.push(line),
    };
  };
  const chart = { addCandlestickSeries: options => add('candles', options),
    addHistogramSeries: options => add('histogram', options), addLineSeries: options => add('line', options),
    priceScale: () => ({ applyOptions() {} }), timeScale: () => timeScale,
    resize() {}, remove() { removed++; } };
  const module = load('components/InteractiveChart.tsx', {
    react, 'react/jsx-runtime': { jsx: () => null, jsxs: () => null },
    'lightweight-charts': { createChart: () => chart, ColorType: { Solid: 'solid' },
      CrosshairMode: { Magnet: 1 }, LineStyle: { Dashed: 2, Dotted: 1, Solid: 0 } },
  }, { ResizeObserver: class { observe() {} disconnect() {} } });
  return {
    records,
    render(data, fullscreen = false) {
      index = 0;
      pending = [];
      module.InteractiveChart({ data, fullscreen, onToggleFullscreen() {} });
      pending.forEach(callback => callback());
    },
    unmount: () => effects.forEach(effect => effect?.cleanup?.()),
    get visible() { return visible; },
    get fitCount() { return fitCount; },
    get removed() { return removed; },
    get followed() { return followed; },
  };
}

function snapshot() {
  return { symbol: 'BTC-USDT', interval: '1m', range: '6H',
    candles: [1, 2, 3].map(time => ({ time, open: 10, high: 12, low: 9, close: 11, volume: 100 })),
    indicators: { ema12: [{ time: 3, value: 10 }] },
    position: { side: 'LONG', entry_price: 10, sl: 8, tp: 15, pnl: 0 },
  };
}

test('Полный снимок обновляет только volume, прошлые свечи, индикаторы и линии позиции', () => {
  const harness = chartHarness();
  const first = snapshot();
  harness.render(first);
  const second = snapshot();
  second.candles[0].volume = 999;
  second.candles[0].close = 10.5;
  second.indicators.ema12[0].value = 11.5;
  second.indicators.macd_signal = [{ time: 3, value: 0.5 }];
  second.position.sl = 7;
  harness.render(second);
  const candles = harness.records.find(record => record.kind === 'candles');
  const volume = harness.records.find(record => record.options.priceScaleId === 'volume');
  const ema = harness.records.find(record => record.options.title === 'EMA 12');
  const signal = harness.records.find(record => record.options.title === 'Signal');
  assert.equal(candles.data[0].close, 10.5);
  assert.equal(volume.data[0].value, 999);
  assert.equal(ema.data[0].value, 11.5);
  assert.equal(signal.data[0].value, 0.5);
  assert.equal(candles.removedLines.length, 3);
  assert.equal(candles.lines[candles.lines.length - 2].price, 7);
  assert.equal(harness.fitCount, 1);
  assert.equal(harness.visible.from, 1);
  assert.equal(harness.visible.to, 2);
  assert.equal(harness.followed, 0);
  const third = snapshot();
  third.candles = third.candles.slice(1);
  third.indicators = {};
  third.position = null;
  harness.render(third);
  assert.equal(candles.data.length, 2);
  assert.equal(ema.data.length, 0);
  assert.equal(signal.data.length, 0);
  assert.equal(candles.removedLines.length, 6);
  harness.render({ ...third, interval: '5m' }, true);
  assert.equal(harness.fitCount, 2);
  harness.unmount();
  assert.equal(harness.removed, 1);
});
