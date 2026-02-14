import { useEffect, useState, useCallback } from 'react';
import { getConfig, updateConfig, validateConfig } from '../api/client';
import { Spinner } from '../components/Spinner';

const STRATEGY_OPTIONS = ['SCALP', 'INTRADAY', 'SWING', 'GRID', 'HYBRID'];

type SaveResult = {
  changes?: { hot_reloadable: string[]; restart_required: string[] };
  needs_restart?: boolean;
};

export function Settings() {
  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);
  const [editing, setEditing] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      setError(null);
      const data = await getConfig();
      setConfig(data);
    } catch (err) {
      console.error('Config fetch error:', err);
      setError(err instanceof Error ? err.message : 'Failed to load config');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    setMessage(null);
    setValidationErrors([]);
    try {
      // Validate first
      const validation = await validateConfig(config);
      if (!validation.valid) {
        setValidationErrors(validation.errors);
        setSaving(false);
        try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error'); } catch {}
        return;
      }

      const result = await updateConfig(config) as SaveResult;
      const restartNeeded = result.needs_restart;
      const hotChanges = result.changes?.hot_reloadable?.length || 0;

      if (restartNeeded) {
        setMessage({
          type: 'warning',
          text: `Saved. ${hotChanges} settings will hot-reload. Restart required for: ${result.changes?.restart_required?.join(', ')}`,
        });
      } else {
        setMessage({
          type: 'success',
          text: hotChanges > 0
            ? `Saved. ${hotChanges} settings will be applied within 30s.`
            : 'Configuration saved.',
        });
      }
      setEditing(false);
      try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success'); } catch {}
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to save configuration' });
      try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error'); } catch {}
    } finally {
      setSaving(false);
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const updateField = (key: string, value: any) => {
    if (!config) return;
    setConfig({ ...config, [key]: value });
  };

  const updateNestedField = (parent: string, key: string, value: any) => {
    if (!config) return;
    setConfig({
      ...config,
      [parent]: { ...config[parent], [key]: value },
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  if (error || !config) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <span className="text-sm text-red-400">{error || 'Failed to load config'}</span>
        <button onClick={fetchConfig} className="text-xs px-4 py-2 bg-tg-section-bg text-tg-hint rounded-lg">
          Retry
        </button>
      </div>
    );
  }

  const preset = config.STYLE_PRESETS?.[config.STRATEGY_STYLE] || {};

  return (
    <div className="flex flex-col gap-4 p-4 pb-24">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold text-tg-text">Settings</span>
        <button
          onClick={() => { setEditing(!editing); setValidationErrors([]); }}
          className={`text-xs px-3 py-1.5 rounded-lg ${
            editing ? 'bg-amber-500/20 text-amber-400' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          {editing ? 'Cancel' : 'Edit'}
        </button>
      </div>

      {message && (
        <div className={`text-sm px-3 py-2 rounded-lg ${
          message.type === 'success' ? 'bg-green-500/20 text-green-400' :
          message.type === 'warning' ? 'bg-amber-500/20 text-amber-400' :
          'bg-red-500/20 text-red-400'
        }`}>
          {message.text}
        </div>
      )}

      {validationErrors.length > 0 && (
        <div className="text-sm px-3 py-2 rounded-lg bg-red-500/20 text-red-400">
          <div className="font-medium mb-1">Validation errors:</div>
          {validationErrors.map((e, i) => (
            <div key={i}>- {e}</div>
          ))}
        </div>
      )}

      {/* Strategy */}
      <Section title="Strategy" badge="hot-reload">
        <div className="flex items-center justify-between">
          <span className="text-sm text-tg-text">Style</span>
          {editing ? (
            <select
              value={config.STRATEGY_STYLE || ''}
              onChange={(e) => updateField('STRATEGY_STYLE', e.target.value)}
              className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 border border-white/10"
            >
              {STRATEGY_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          ) : (
            <span className="text-sm text-tg-button font-medium">{config.STRATEGY_STYLE}</span>
          )}
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-tg-text">Timeframe</span>
          <span className="text-sm text-tg-hint">{preset.timeframe || 'N/A'}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-tg-text">Leverage</span>
          {editing ? (
            <div className="flex items-center gap-1">
              <input
                type="number"
                min={1}
                max={125}
                step={1}
                value={preset.leverage ?? ''}
                onChange={(e) => {
                  const v = parseInt(e.target.value) || 1;
                  const style = config.STRATEGY_STYLE;
                  if (!style) return;
                  updateNestedField('STYLE_PRESETS', style, {
                    ...config.STYLE_PRESETS?.[style],
                    leverage: v,
                  });
                }}
                className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 w-20 text-right border border-white/10"
              />
              <span className="text-sm text-tg-hint">x</span>
            </div>
          ) : (
            <span className="text-sm text-tg-hint">{preset.leverage || 'N/A'}x</span>
          )}
        </div>
      </Section>

      {/* Position & Risk */}
      <Section title="Position & Risk" badge="hot-reload">
        <SettingRow label="Position Size %" value={config.POSITION_SIZE_PERCENT} editing={editing} onChange={(v) => updateField('POSITION_SIZE_PERCENT', Number(v))} />
        <SettingRow label="Min Trade (USDT)" value={config.MIN_TRADE_AMOUNT_USDT} editing={editing} onChange={(v) => updateField('MIN_TRADE_AMOUNT_USDT', Number(v))} />
        <SettingRow label="Min Confidence" value={config.MIN_CONFIDENCE_THRESHOLD} editing={editing} step="0.05" onChange={(v) => updateField('MIN_CONFIDENCE_THRESHOLD', Number(v))} />
        <SettingRow label="Min R/R Ratio" value={config.MIN_RISK_REWARD_RATIO} editing={editing} step="0.1" onChange={(v) => updateField('MIN_RISK_REWARD_RATIO', Number(v))} />
        <SettingRow label="Take Profit %" value={config.TAKE_PROFIT_PERCENT} editing={editing} step="0.1" onChange={(v) => updateField('TAKE_PROFIT_PERCENT', Number(v))} />
        <SettingRow label="Stop Loss %" value={config.STOP_LOSS_PERCENT} editing={editing} step="0.1" onChange={(v) => updateField('STOP_LOSS_PERCENT', Number(v))} />
      </Section>

      {/* AI Settings */}
      <Section title="AI Settings" badge="hot-reload">
        <SettingRow
          label="Model"
          value={config.AI_SETTINGS?.model}
          editing={editing}
          type="text"
          onChange={(v) => updateNestedField('AI_SETTINGS', 'model', v)}
        />
        <SettingRow
          label="Temperature"
          value={config.AI_SETTINGS?.temperature}
          editing={editing}
          step="0.1"
          onChange={(v) => updateNestedField('AI_SETTINGS', 'temperature', Number(v))}
        />
        <SettingRow
          label="Max Tokens"
          value={config.AI_SETTINGS?.max_tokens}
          editing={editing}
          step="1"
          onChange={(v) => updateNestedField('AI_SETTINGS', 'max_tokens', parseInt(v) || 0)}
        />
        <SettingRow
          label="Timeout (s)"
          value={config.AI_SETTINGS?.request_timeout}
          editing={editing}
          step="1"
          onChange={(v) => updateNestedField('AI_SETTINGS', 'request_timeout', parseInt(v) || 60)}
        />
      </Section>

      {/* Toggles */}
      <Section title="Features" badge="hot-reload">
        <ToggleRow label="Aggressive Mode" value={!!config.AGGRESSIVE_MODE} editing={editing} onChange={(v) => updateField('AGGRESSIVE_MODE', v)} />
        <ToggleRow label="News Enabled" value={config.ENABLE_NEWS !== false} editing={editing} onChange={(v) => updateField('ENABLE_NEWS', v)} />
        <ToggleRow label="AI Skip on RSI" value={config.ENABLE_AI_SKIP_ON_RSI !== false} editing={editing} onChange={(v) => updateField('ENABLE_AI_SKIP_ON_RSI', v)} />
      </Section>

      {/* Symbols (restart required) */}
      <Section title="Symbols" badge="restart">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(config.EXCHANGE_SYMBOLS || {}).map(([exchange, syms]) =>
            (syms as string[]).map((s) => (
              <span key={`${exchange}-${s}`} className="text-xs bg-tg-bg px-2 py-1 rounded text-tg-text">
                {s}
              </span>
            ))
          )}
        </div>
      </Section>

      {/* Disabled Symbols */}
      {(config.DISABLED_SYMBOLS?.length > 0) && (
        <Section title="Disabled Symbols" badge="hot-reload">
          <div className="flex flex-wrap gap-1.5">
            {(config.DISABLED_SYMBOLS as string[]).map((s) => (
              <span key={s} className="text-xs bg-red-500/20 text-red-400 px-2 py-1 rounded">
                {s}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Save button */}
      {editing && (
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-3 bg-tg-button text-white font-medium rounded-xl disabled:opacity-50 transition-opacity"
        >
          {saving ? 'Saving...' : 'Save Configuration'}
        </button>
      )}
    </div>
  );
}

function Section({ title, badge, children }: {
  title: string;
  badge?: 'hot-reload' | 'restart';
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-tg-hint">{title}</h3>
        {badge === 'hot-reload' && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">live</span>
        )}
        {badge === 'restart' && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">restart</span>
        )}
      </div>
      <div className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-3">
        {children}
      </div>
    </section>
  );
}

function SettingRow({ label, value, editing, onChange, type = 'number', step }: {
  label: string;
  value: any;
  editing: boolean;
  onChange: (v: string) => void;
  type?: 'number' | 'text';
  step?: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-tg-text">{label}</span>
      {editing ? (
        <input
          type={type}
          step={type === 'number' ? (step || 'any') : undefined}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 w-32 text-right border border-white/10"
        />
      ) : (
        <span className="text-sm text-tg-hint">{value ?? 'N/A'}</span>
      )}
    </div>
  );
}

function ToggleRow({ label, value, editing, onChange }: {
  label: string;
  value: boolean;
  editing: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-tg-text">{label}</span>
      {editing ? (
        <button
          onClick={() => onChange(!value)}
          className={`text-xs px-3 py-1 rounded-lg transition-colors ${
            value ? 'bg-green-500/20 text-green-400' : 'bg-tg-bg text-tg-hint'
          }`}
        >
          {value ? 'ON' : 'OFF'}
        </button>
      ) : (
        <span className={`text-sm ${value ? 'text-green-400' : 'text-tg-hint'}`}>
          {value ? 'ON' : 'OFF'}
        </span>
      )}
    </div>
  );
}
