// Регрессии редактора execution: defaults, ограничения и ошибки конкретных полей.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'src/telegram_panel/frontend/node_modules/typescript'));
const source = fs.readFileSync(path.join(root, 'src/telegram_panel/frontend/src/components/config/executionForm.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
} }).outputText;
const exportsObject = {};
vm.runInNewContext(code, { exports: exportsObject });
const { withExecutionDefaults, validateExecutionForm, executionStartWarning } = exportsObject;

function plain(value) { return JSON.parse(JSON.stringify(value)); }

test('Безопасный default и полные вложенные поля при частичном ответе', () => {
  const settings = withExecutionDefaults({ protection: { recovery_attempts: 5 }, market_data: { history_limit: 1000 } });
  assert.equal(settings.execution_mode, 'read_only');
  assert.equal(settings.account_id, 'default');
  assert.deepEqual(plain(settings.spot), { quote_budget_usdt: 0, allow_local_protection: false });
  assert.deepEqual(plain(settings.protection), { trigger_price: 'mark', failure_policy: 'pause', verification_timeout_seconds: 10, recovery_attempts: 5 });
  assert.deepEqual(plain(settings.market_data), { max_age_seconds: 30, reconcile_interval_seconds: 60, history_limit: 1000 });
  assert.deepEqual(plain(validateExecutionForm(settings)), {});
});

test('Spot defaults и MEXC demo отражают только чтение, не sandbox', () => {
  const settings = withExecutionDefaults({ exchange: 'mexc', market_type: 'spot', execution_mode: 'demo' });
  assert.equal(settings.execution_mode, 'read_only');
  assert.equal(settings.position_mode, 'one_way');
  assert.equal(settings.grid_mode, 'inventory');
  assert.equal(settings.protection.trigger_price, 'last');
  assert.deepEqual(plain(validateExecutionForm(settings)), {});
});

test('Ошибка каждого числового поля: пусто, bool, дробь и выход за границы', () => {
  const cases = [
    ['protection', 'verification_timeout_seconds', NaN], ['protection', 'recovery_attempts', -1],
    ['market_data', 'max_age_seconds', true], ['market_data', 'reconcile_interval_seconds', 1.5],
    ['market_data', 'history_limit', 2001],
  ];
  for (const [section, key, value] of cases) {
    const settings = withExecutionDefaults({});
    settings[section][key] = value;
    const errors = validateExecutionForm(settings);
    assert.deepEqual(Object.keys(errors), [`${section}.${key}`]);
    assert.ok(errors[`${section}.${key}`].includes('целое число'));
  }
});

test('Cross, BingX Spot, hedge на Spot и hedged one-way имеют адресные ошибки', () => {
  assert.ok(validateExecutionForm(withExecutionDefaults({ margin_mode: 'cross' })).margin_mode);
  assert.ok(validateExecutionForm(withExecutionDefaults({ market_type: 'spot' })).market_type);
  const spot = withExecutionDefaults({ exchange: 'mexc', market_type: 'spot', position_mode: 'hedge', grid_mode: 'hedged', protection: { trigger_price: 'mark' } });
  const errors = validateExecutionForm(spot);
  assert.ok(errors.position_mode);
  assert.ok(errors.grid_mode);
  assert.ok(errors['protection.trigger_price']);
  assert.ok(validateExecutionForm(withExecutionDefaults({ position_mode: 'one_way', grid_mode: 'hedged' })).grid_mode);
});

test('Spot TOTAL budget: дробный бюджет допустим, disabled можно сохранить, readiness отдельно', () => {
  const settings = withExecutionDefaults({ exchange: 'mexc', market_type: 'spot', execution_mode: 'live', spot: { quote_budget_usdt: 0 } });
  assert.equal(settings.spot.allow_local_protection, false);
  assert.deepEqual(plain(validateExecutionForm(settings)), {});
  assert.ok(executionStartWarning(settings).includes('бюджет'));
  settings.spot.quote_budget_usdt = 100.25;
  assert.deepEqual(plain(validateExecutionForm(settings)), {});
  assert.ok(executionStartWarning(settings).includes('риска'));
  settings.spot.allow_local_protection = true;
  assert.equal(executionStartWarning(settings), null);
  settings.execution_mode = 'read_only';
  settings.spot.quote_budget_usdt = 0;
  assert.equal(executionStartWarning(settings), null);
});

test('Адресные ошибки account_id, Spot budget и bool-согласия', () => {
  for (const account_id of ['', 'API:key', 'Upper', '../default', 'a'.repeat(65), 'a\n']) {
    assert.ok(validateExecutionForm(withExecutionDefaults({ account_id })).account_id);
  }
  for (const quote_budget_usdt of [-1, NaN, Infinity, true, '100']) {
    assert.ok(validateExecutionForm(withExecutionDefaults({ spot: { quote_budget_usdt } }))['spot.quote_budget_usdt']);
  }
  assert.ok(validateExecutionForm(withExecutionDefaults({ spot: { allow_local_protection: 'true' } }))['spot.allow_local_protection']);
  assert.deepEqual(plain(validateExecutionForm(withExecutionDefaults({ account_id: 'account_2-test' }))), {});
});

test('Допустимые границы не превращаются в ошибку', () => {
  const settings = withExecutionDefaults({ protection: { verification_timeout_seconds: 300, recovery_attempts: 0 }, market_data: { max_age_seconds: 1, reconcile_interval_seconds: 3600, history_limit: 50 } });
  assert.deepEqual(plain(validateExecutionForm(settings)), {});
});
