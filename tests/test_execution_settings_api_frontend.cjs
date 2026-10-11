// HTTP редактора execution: токен только в headers, ошибки не скрываются.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'src/telegram_panel/frontend/node_modules/typescript'));

function api(window, fetch) {
  const source = fs.readFileSync(path.join(root, 'src/telegram_panel/frontend/src/components/config/executionApi.ts'), 'utf8').replaceAll('import.meta.env', '({})');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, window, fetch, URLSearchParams });
  return exports;
}

test('Роутированный hash /auth?token поддерживается без токена в URL запроса', async () => {
  const calls = [];
  const client = api({ location: { origin: 'https://panel.example', hash: '#/auth?token=fake-token', search: '' } }, async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ execution_mode: 'read_only' }) };
  });
  await client.getExecutionSettings();
  assert.equal(calls[0].options.headers['X-Web-Token'], 'fake-token');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].url, 'https://panel.example/api/config/execution');
});

test('Telegram initData имеет приоритет над web token', async () => {
  const client = api({ location: { origin: 'https://panel.example', hash: '#token=fake-token', search: '' }, Telegram: { WebApp: { initData: 'fake-signed-data' } } }, async (_, options) => {
    assert.equal(options.headers['X-Telegram-Init-Data'], 'fake-signed-data');
    assert.equal(options.headers['X-Web-Token'], undefined);
    assert.equal(options.method, 'PUT');
    assert.equal(JSON.parse(options.body).execution_mode, 'read_only');
    return { ok: true, json: async () => ({ status: 'ok' }) };
  });
  await client.updateExecutionSettings({ execution_mode: 'read_only' });
});

test('Блокировка 409 сохраняет серверную причину', async () => {
  const client = api({ location: { origin: 'https://panel.example', hash: '', search: '' } }, async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Остановите runtime' }) }));
  await assert.rejects(client.updateExecutionSettings({ execution_mode: 'live' }), error => error.message === 'Остановите runtime');
});

test('401 сообщает о необходимости новой ссылки', async () => {
  const client = api({ location: { origin: 'https://panel.example', hash: '#/auth?token=fake-token', search: '' } }, async () => ({ ok: false, status: 401 }));
  await assert.rejects(client.getExecutionSettings(), error => error.message.includes('/weblink'));
});
