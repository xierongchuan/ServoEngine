// Поведенческие регрессии ИИ-панели без браузера и дополнительных зависимостей.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'src/telegram_panel/frontend');
const ts = require(path.join(frontend, 'node_modules/typescript'));

function moduleAt(relative, modules = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(frontend, 'src', relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { exports, URL, AbortController, Error, console,
    window: { confirm: () => true }, require(name) { assert.ok(modules[name], `Неожиданный импорт: ${name}`); return modules[name]; },
  });
  return exports;
}
const form = moduleAt('components/config/aiForm.ts');
const plain = value => JSON.parse(JSON.stringify(value));
function settings() {
  const result = form.defaultAISettings();
  result.providers.openrouter.model = 'deepseek/deepseek-chat';
  result.providers.lmstudio.model = 'local-model';
  return result;
}
function response(value = settings()) {
  return { settings: value, providers: ['openrouter', 'deepseek', 'lmstudio'].map(id => ({
    id, label: id, requires_api_key: id !== 'lmstudio', has_api_key: id !== 'lmstudio', configured: true,
  })) };
}

function harness() {
  const hooks = [], effects = [], requests = [];
  let cursor = 0, tree, stopped = false, writesAfterStop = 0, focusCount = 0;
  const react = {
    useId: () => 'ai-test',
    useRef(initial) {
      const index = cursor++;
      return hooks[index] || (hooks[index] = { current: initial });
    },
    useState(initial) {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[index].value, next => {
        if (stopped) writesAfterStop++;
        hooks[index].value = typeof next === 'function' ? next(hooks[index].value) : next;
      }];
    },
    useEffect(callback, dependencies) {
      const index = cursor++, previous = hooks[index];
      if (!previous || dependencies.some((item, i) => item !== previous.dependencies[i])) {
        effects.push(() => { previous?.cleanup?.(); hooks[index] = { dependencies, cleanup: callback() }; });
      }
    },
  };
  const queue = (kind, data, signal) => new Promise((resolve, reject) => requests.push({ kind, data, signal, resolve, reject }));
  const jsx = (type, props) => ({ type, props });
  const component = moduleAt('components/config/AISettings.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, './aiForm': form,
    '../../api/ai': {
      getAISettings: signal => queue('load', null, signal),
      saveAISettings: (data, signal) => queue('save', data, signal),
      probeAIProvider: (data, signal) => queue('probe', data, signal),
      getAIModels: (data, signal) => queue('models', data, signal),
      aiErrorMessage: () => 'Безопасная ошибка запроса',
    },
  });
  function render() {
    cursor = 0; tree = component.AISettings();
    for (const node of nodes(tree)) {
      if (node.props?.ref) node.props.ref.current = { focus() { focusCount++; } };
    }
    while (effects.length) effects.shift()();
  }
  function nodes(node) {
    if (!node || typeof node !== 'object') return [];
    if (Array.isArray(node)) return node.flatMap(item => nodes(item));
    return [node, ...nodes(node.props?.children)];
  }
  function text(node) {
    if (Array.isArray(node)) return node.map(text).join('');
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    return node?.props ? text(node.props.children) : '';
  }
  const button = label => nodes(tree).find(node => node.type === 'button' && text(node) === label);
  const field = key => nodes(tree).find(node => ['input', 'select', 'textarea'].includes(node.type) && node.props.id === `ai-test-${key}`);
  const change = (key, value) => { assert.ok(field(key), key); field(key).props.onChange({ target: { value } }); render(); };
  const check = (key, checked) => { assert.ok(field(key), key); field(key).props.onChange({ target: { checked } }); render(); };
  const click = label => { assert.ok(button(label), label); assert.equal(!!button(label).props.disabled, false, label); button(label).props.onClick(); render(); };
  const submit = () => { nodes(tree).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); render(); };
  render();
  return { render, requests, nodes: () => nodes(tree), text: () => text(tree), button, field, change, check, click, submit,
    stop() { stopped = true; hooks.forEach(hook => hook?.cleanup?.()); },
    writesAfterStop: () => writesAfterStop, focusCount: () => focusCount,
  };
}
async function flush(h) { for (let i = 0; i < 8; i++) await Promise.resolve(); h.render(); }
async function loaded(value) { const h = harness(); h.requests[0].resolve(value || response()); await flush(h); return h; }

test('Независимые профили сохраняются при переключении; отмена и сброс не пишут на сервер', async () => {
  const h = await loaded();
  h.change('providers.openrouter.model', 'vendor/new-model');
  h.click('DeepSeek');
  h.change('providers.deepseek.base_url', 'https://deepseek.example/v1');
  h.click('LM Studio');
  h.change('providers.lmstudio.model', 'another-local');
  h.click('OpenRouter');
  assert.equal(h.field('providers.openrouter.model').props.value, 'vendor/new-model');
  assert.equal(h.field('providers.deepseek.base_url').props.value, 'https://deepseek.example/v1');
  assert.equal(h.field('providers.lmstudio.model').props.value, 'another-local');
  h.click('Отменить');
  assert.equal(h.field('providers.openrouter.model').props.value, 'deepseek/deepseek-chat');
  h.click('Сбросить черновик');
  assert.equal(h.field('providers.lmstudio.base_url').props.value, 'http://host.containers.internal:1234/v1');
  assert.equal(h.field('providers.lmstudio.model').props.value, '');
  assert.deepEqual(h.requests.map(item => item.kind), ['load']);
  h.stop();
});

test('Проверка saved профиля блокируется только изменением его URL/model, не переключением или температурой', async () => {
  const h = await loaded();
  h.click('DeepSeek');
  assert.equal(h.button('Проверить соединение').props.disabled, false);
  h.change('temperature', '0.5');
  assert.equal(h.button('Проверить соединение').props.disabled, false);
  h.change('providers.deepseek.model', 'draft-model');
  assert.equal(h.button('Проверить соединение').props.disabled, true);
  assert.equal(h.button('Обновить модели').props.disabled, true);
  h.click('OpenRouter');
  assert.equal(h.button('Проверить соединение').props.disabled, false);
  h.stop();
});

test('Смена профиля отменяет probe; поздний ответ и unmount не меняют состояние', async () => {
  const h = await loaded();
  h.click('Проверить соединение');
  const old = h.requests[1];
  assert.equal(old.data, 'openrouter');
  assert.equal(h.button('Обновить модели').props.disabled, true);
  h.click('DeepSeek');
  assert.equal(old.signal.aborted, true);
  h.click('Проверить соединение');
  const newer = h.requests[2];
  newer.resolve({ provider: 'deepseek', ok: true, latency_ms: 10, message: 'Новый ответ' });
  await flush(h);
  old.resolve({ provider: 'openrouter', ok: false, latency_ms: 30, message: 'Устаревший ответ' });
  await flush(h);
  assert.ok(h.text().includes('Новый ответ'));
  assert.ok(!h.text().includes('Устаревший ответ'));
  h.click('Проверить соединение');
  const last = h.requests[3];
  h.stop();
  assert.equal(last.signal.aborted, true);
  last.resolve({ provider: 'deepseek', ok: true, latency_ms: 10, message: 'После unmount' });
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(h.writesAfterStop(), 0);
});

test('Обновление моделей: запрет overlap до rerender, отмена по редактированию, ручной ID при ошибке', async () => {
  const h = await loaded();
  const action = h.button('Обновить модели').props.onClick;
  action(); action(); h.render();
  assert.equal(h.requests.length, 2);
  h.change('providers.openrouter.base_url', 'https://other.example/v1');
  assert.equal(h.requests[1].signal.aborted, true);
  h.requests[1].resolve({ provider: 'openrouter', models: [{ id: 'stale-model' }] });
  await flush(h);
  assert.ok(!h.nodes().some(node => node.type === 'option' && node.props.value === 'stale-model'));
  h.click('Отменить');
  h.click('Обновить модели');
  h.requests[2].reject(new Error('secret-not-to-display'));
  await flush(h);
  assert.ok(h.text().includes('ID модели можно ввести вручную'));
  assert.ok(!h.text().includes('secret-not-to-display'));
  h.change('providers.openrouter.model', 'manual/model');
  assert.equal(h.field('providers.openrouter.model').props.value, 'manual/model');
  h.stop();
});

test('Каталог не заменяет вручную заданную модель и удаляет дубликаты', async () => {
  const h = await loaded();
  h.click('Обновить модели');
  h.requests[1].resolve({ provider: 'openrouter', models: [{ id: 'b' }, { id: 'a' }, { id: 'b' }] });
  await flush(h);
  const catalog = h.nodes().find(node => node.type === 'datalist');
  assert.deepEqual(Array.from(catalog.props.children, node => node.props.value), ['a', 'b']);
  assert.equal(h.field('providers.openrouter.model').props.value, 'deepseek/deepseek-chat');
  h.stop();
});

test('Сохранение canonical payload, сохранение черновика при ошибке и явный контролируемый restart', async () => {
  const h = await loaded();
  h.change('providers.deepseek.model', 'deepseek-v4-pro');
  h.submit();
  const failed = h.requests[1];
  assert.equal(failed.kind, 'save');
  failed.reject(new Error('failure')); await flush(h);
  assert.equal(h.field('providers.deepseek.model').props.value, 'deepseek-v4-pro');
  h.submit();
  const successful = h.requests[2];
  assert.deepEqual(Object.keys(successful.data.providers).sort(), ['deepseek', 'lmstudio', 'openrouter']);
  assert.ok(!JSON.stringify(successful.data).includes('api_key'));
  assert.ok(h.nodes().filter(node => node.type === 'fieldset').every(node => node.props.disabled));
  successful.resolve({ settings: successful.data, needs_restart: true }); await flush(h);
  assert.ok(h.text().includes('контролируемый перезапуск'));
  assert.ok(h.text().includes('не запускает и не перезапускает процессы автоматически'));
  assert.equal(h.button('Сохранить настройки ИИ').props.disabled, true);
  assert.deepEqual(h.requests.map(item => item.kind), ['load', 'save', 'save']);
  h.stop();
});

test('Пустые числа не превращаются в ноль: inline ошибки и раскрытие разделов без PUT', async () => {
  const h = await loaded();
  h.change('request_timeout', '');
  h.field('request_timeout').props.onBlur(); h.render();
  assert.equal(h.field('request_timeout').props['aria-invalid'], true);
  h.submit();
  assert.equal(h.requests.length, 1);
  assert.ok(h.text().includes('Проверьте поля перед сохранением'));
    assert.equal(h.focusCount(), 1);
  assert.equal(h.button('Расширенные параметры−').props['aria-expanded'], true);
  assert.ok(h.nodes().some(node => node.type === 'a' && node.props.href === '#ai-test-request_timeout'));
  h.stop();
});

test('Отсутствующий required key отключает probe, но не мешает сохранить профиль; LM Studio key optional', async () => {
  const value = response(); value.providers[0].has_api_key = false;
  const h = await loaded(value);
  assert.equal(h.button('Проверить соединение').props.disabled, true);
  assert.ok(h.text().includes('OPENROUTER_API_KEY'));
  assert.ok(!h.nodes().some(node => node.type === 'input' && node.props.type === 'password'));
  h.click('LM Studio');
  assert.equal(h.button('Проверить соединение').props.disabled, false);
  h.submit(); assert.equal(h.requests[1].kind, 'save');
  h.stop();
});

test('VETO/REGIME наследуют provider/model независимо; defaults изолированы от saved', () => {
  const saved = settings(), draft = form.toAIDraft(saved);
  draft.overrides.veto.provider = 'deepseek';
  assert.deepEqual(plain(form.effectiveAIOverride(form.fromAIDraft(draft), draft.overrides.veto)), { provider: 'deepseek', model: 'deepseek-flash' });
  draft.overrides.regime.model = 'explicit-model';
  draft.provider = 'lmstudio';
  assert.deepEqual(plain(form.effectiveAIOverride(form.fromAIDraft(draft), draft.overrides.regime)), { provider: 'lmstudio', model: 'explicit-model' });
  draft.providers.deepseek.model = 'new-model';
  assert.equal(saved.providers.deepseek.model, 'deepseek-flash');
  assert.equal(saved.overrides.veto.provider, null);
  assert.deepEqual(plain(form.validateAIDraft(draft)), {});
});

test('Валидация URLs без секретов, ID, числовых границ и fallback моделей адресна', () => {
  for (const url of ['file:///app/key', 'https://user:secret@example.test/v1', 'https://example.test/v1?key=secret', 'https://example.test/v1#key']) {
    const draft = form.toAIDraft(settings()); draft.providers.deepseek.base_url = url;
    assert.ok(form.validateAIDraft(draft)['providers.deepseek.base_url']);
  }
  const draft = form.toAIDraft(settings());
  draft.retry_count = 1.5; draft.temperature = NaN; draft.max_tokens = 0;
  draft.providers.openrouter.model = 'with space';
  draft.fallback_models = 'model/one\nmodel/one\nmodel/two';
  const errors = form.validateAIDraft(draft);
  assert.ok(errors.retry_count); assert.ok(errors.temperature); assert.ok(errors.max_tokens);
  assert.ok(errors['providers.openrouter.model']);
  assert.deepEqual(plain(form.fromAIDraft(draft).fallback_models), ['model/one', 'model/two']);
});

test('Первичная ошибка загрузки доступна для retry; поздние load/save после unmount игнорируются', async () => {
  const initial = harness();
  initial.requests[0].reject(new Error('private-server-detail')); await flush(initial);
  assert.ok(initial.text().includes('Безопасная ошибка запроса'));
  initial.click('Повторить загрузку');
  const load = initial.requests[1]; initial.stop();
  assert.equal(load.signal.aborted, true);
  load.resolve(response());
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(initial.writesAfterStop(), 0);

  const saving = await loaded();
  saving.change('temperature', '0.4'); saving.submit();
  const save = saving.requests[1]; saving.stop();
  assert.equal(save.signal.aborted, true);
  save.resolve({ settings: save.data, needs_restart: true });
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(saving.writesAfterStop(), 0);
});

test('После сохранения ранее пустого LM Studio статус configured соответствует canonical ответу', async () => {
  const value = response(); value.settings.providers.lmstudio.model = ''; value.providers[2].configured = false;
  const h = await loaded(value);
  assert.ok(h.text().includes('Сохранённый профиль ещё не настроен'));
  h.click('LM Studio'); h.change('providers.lmstudio.model', 'loaded-model'); h.submit();
  h.requests[1].resolve({ settings: h.requests[1].data, needs_restart: true }); await flush(h);
  assert.ok(!h.text().includes('Сохранённый профиль ещё не настроен'));
  assert.equal(h.button('Проверить соединение').props.disabled, false);
  h.stop();
});

test('Границы и расширенные effort согласованы с canonical backend; секреты и HTTP cloud отклоняются', () => {
  const draft = form.toAIDraft(form.defaultAISettings());
  assert.deepEqual(plain(form.validateAIDraft(draft)), {});
  draft.max_tokens = 1000000; draft.request_timeout = 300; draft.retry_count = 5; draft.retry_backoff_base = 10;
  for (const effort of ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']) {
    draft.reasoning.effort = effort;
    assert.deepEqual(plain(form.validateAIDraft(draft)), {});
  }
  draft.request_timeout = 301; draft.retry_count = 6; draft.retry_backoff_base = 11;
  draft.providers.openrouter.base_url = 'http://example.test/v1';
  draft.providers.deepseek.model = 'sk-synthetic-secret';
  draft.fallback_models = Array.from({ length: 21 }, (_, i) => `model/${i}`).join('\n');
  const errors = form.validateAIDraft(draft);
  for (const key of ['request_timeout', 'retry_count', 'retry_backoff_base', 'providers.openrouter.base_url', 'providers.deepseek.model', 'fallback_models']) assert.ok(errors[key], key);
});

test('Новые defaults DeepSeek: официальный корень API, deepseek-flash и thinking выключен', () => {
  const defaults = form.defaultAISettings();
  assert.deepEqual(plain(defaults.providers.deepseek), {
    base_url: 'https://api.deepseek.com', model: 'deepseek-flash', thinking: { enabled: false, effort: 'high' },
  });
  assert.equal(defaults.overrides.veto.max_tokens, 100);
  assert.ok(!('thinking' in defaults.providers.openrouter));
  assert.ok(!('thinking' in defaults.providers.lmstudio));
  assert.deepEqual(plain(form.validateAIDraft(form.toAIDraft(defaults))), {});
});

test('Legacy DeepSeek URL/model без thinking сохраняются точно, без нового поля и автосохранения', async () => {
  const saved = settings(); saved.providers.deepseek = { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' };
  const draft = form.toAIDraft(saved);
  assert.deepEqual(plain(form.fromAIDraft(draft).providers.deepseek), plain(saved.providers.deepseek));
  assert.deepEqual(plain(form.validateAIDraft(draft)), {});
  const h = await loaded(response(saved));
  assert.equal(h.button('Сохранить настройки ИИ').props.disabled, true);
  h.click('DeepSeek');
  assert.equal(h.field('providers.deepseek.base_url').props.value, 'https://api.deepseek.com/v1');
  assert.equal(h.field('providers.deepseek.model').props.value, 'deepseek-chat');
  assert.equal(h.field('providers.deepseek.thinking.enabled').props.checked, false);
  h.submit();
  assert.deepEqual(plain(h.requests[1].data.providers.deepseek), plain(saved.providers.deepseek));
  assert.deepEqual(h.requests.map(item => item.kind), ['load', 'save']);
  h.stop();
});

test('Thinking редактируется в карточке, сохраняется только в DeepSeek и не меняет OR reasoning или роли', async () => {
  const original = settings();
  const h = await loaded(response(original));
  h.click('DeepSeek');
  assert.equal(h.button('Thinking DeepSeek · выключен+').props['aria-expanded'], false);
  h.click('Thinking DeepSeek · выключен+');
  assert.equal(h.button('Thinking DeepSeek · выключен−').props['aria-expanded'], true);
  h.check('providers.deepseek.thinking.enabled', true);
  h.change('providers.deepseek.thinking.effort', 'max');
  assert.ok(h.text().includes('игнорирует температуру'));
  assert.ok(h.text().includes('Лимит токенов включает рассуждения и итоговый ответ'));
  const card = h.nodes().find(node => node.type === 'section' && node.props['aria-labelledby'] === 'ai-test-deepseek-heading');
  assert.ok(JSON.stringify(card).includes('Есть изменения'));
  h.click('OpenRouter'); h.click('DeepSeek');
  assert.equal(h.field('providers.deepseek.thinking.enabled').props.checked, true);
  assert.equal(h.field('providers.deepseek.thinking.effort').props.value, 'max');
  h.submit();
  const payload = h.requests[1].data;
  assert.deepEqual(plain(payload.providers.deepseek.thinking), { enabled: true, effort: 'max' });
  assert.deepEqual(plain(payload.reasoning), plain(original.reasoning));
  assert.deepEqual(plain(payload.overrides), plain(original.overrides));
  assert.ok(!('thinking' in payload.providers.openrouter));
  assert.ok(!('thinking' in payload.providers.lmstudio));
  assert.equal(original.providers.deepseek.thinking.enabled, false);
  h.requests[1].resolve({ settings: payload, needs_restart: true }); await flush(h);
  assert.equal(h.button('Сохранить настройки ИИ').props.disabled, true);
  h.check('providers.deepseek.thinking.enabled', false);
  h.click('Отменить');
  assert.equal(h.field('providers.deepseek.thinking.enabled').props.checked, true);
  h.click('Сбросить черновик');
  assert.equal(h.field('providers.deepseek.thinking.enabled').props.checked, false);
  assert.equal(h.field('providers.deepseek.thinking.effort').props.value, 'high');
  assert.equal(h.requests.length, 2);
  h.stop();
});

test('Optional thinking глубоко копируется в оба направления; low/high/max валидны независимо от OR effort', () => {
  const saved = settings(); const draft = form.toAIDraft(saved);
  draft.providers.deepseek.thinking.enabled = true;
  draft.providers.deepseek.thinking.effort = 'high';
  assert.equal(saved.providers.deepseek.thinking.enabled, false);
  const canonical = form.fromAIDraft(draft);
  canonical.providers.deepseek.thinking.effort = 'max';
  assert.equal(draft.providers.deepseek.thinking.effort, 'high');
  for (const effort of ['low', 'high', 'max']) {
    draft.providers.deepseek.thinking.effort = effort;
    assert.deepEqual(plain(form.validateAIDraft(draft)), {});
  }
  draft.providers.deepseek.thinking = { enabled: 'true', effort: 'medium' };
  const errors = form.validateAIDraft(draft);
  assert.ok(errors['providers.deepseek.thinking.enabled']);
  assert.ok(errors['providers.deepseek.thinking.effort']);
  assert.ok(!errors['reasoning.effort']);
});

test('Ошибка effort раскрывает DeepSeek thinking, связывает inline ошибку и summary; PUT не выполняется', async () => {
  const h = await loaded();
  h.change('providers.deepseek.thinking.effort', 'medium'); h.submit();
  assert.equal(h.requests.length, 1);
  assert.equal(h.button('Thinking DeepSeek · выключен−').props['aria-expanded'], true);
  assert.equal(h.field('providers.deepseek.thinking.effort').props['aria-invalid'], true);
  assert.ok(h.field('providers.deepseek.thinking.effort').props['aria-describedby'].includes('providers.deepseek.thinking.effort-error'));
  assert.ok(h.nodes().some(node => node.type === 'a' && node.props.href === '#ai-test-providers.deepseek.thinking.effort'));
  assert.equal(h.focusCount(), 1);
  h.stop();
});

test('Thinking наследуется через профиль итогового провайдера VETO; GET-only probe не зависит от draft thinking', async () => {
  const h = await loaded(); h.click('DeepSeek');
  h.check('providers.deepseek.thinking.enabled', true);
  assert.equal(h.button('Проверить соединение').props.disabled, false);
  h.change('overrides.veto.provider', 'deepseek');
  assert.ok(h.text().includes('Thinking берётся из профиля DeepSeek: включён'));
  h.click('Проверить соединение');
  assert.equal(h.requests[1].kind, 'probe');
  assert.equal(h.requests[1].data, 'deepseek');
  h.check('providers.deepseek.thinking.enabled', false);
  assert.equal(h.requests[1].signal.aborted, true);
  assert.ok(h.text().includes('Thinking берётся из профиля DeepSeek: выключен'));
  h.stop();
});

test('Transport использует fetchAPI, передаёт abort и явно запрещает платную генерацию', async () => {
  const calls = [];
  class APIError extends Error { constructor(status, message) { super(message); this.status = status; } }
  class AuthenticationError extends APIError {}
  const api = moduleAt('api/ai.ts', { './client': { APIError, AuthenticationError, fetchAPI: async (...args) => { calls.push(args); return {}; } } });
  const signal = new AbortController().signal;
  await api.getAISettings(signal); await api.saveAISettings(settings(), signal);
  await api.probeAIProvider('deepseek', signal); await api.getAIModels('lmstudio', signal);
  assert.deepEqual(calls.map(call => call[0]), ['/api/config/ai', '/api/config/ai', '/api/config/ai/probe', '/api/config/ai/models?provider=lmstudio']);
  assert.ok(calls.every(call => call[1].signal === signal));
  assert.equal(calls[1][1].method, 'PUT'); assert.equal(calls[2][1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[2][1].body), { provider: 'deepseek', allow_paid_completion: false });
  for (const status of [404, 409, 422, 429, 502, 503, 504, 500]) assert.ok(!api.aiErrorMessage(new APIError(status, 'secret')).includes('secret'));
  assert.equal(api.aiErrorMessage(new AuthenticationError(403, 'Нет доступа')), 'Нет доступа');
});

test('Settings содержит отдельную ИИ вкладку без legacy AI editor и вызовов restart из AISettings', () => {
  const source = fs.readFileSync(path.join(frontend, 'src/pages/Settings.tsx'), 'utf8');
  assert.ok(source.includes("{ value: 'ai', label: 'ИИ' }"));
  assert.ok(source.includes("{tab === 'ai' && <AISettings />}"));
  assert.ok(!source.includes('updateAI'));
  assert.ok(!source.includes('AI Infrastructure'));
  assert.ok(source.includes('<ExecutionSettings />'));
});
