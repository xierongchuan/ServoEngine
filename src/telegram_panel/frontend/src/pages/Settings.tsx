import { useEffect, useState, useCallback } from 'react';
import { getConfig, updateConfig } from '../api/client';
import { Spinner } from '../components/Spinner';

const STRATEGY_OPTIONS = ['SCALP', 'INTRADAY', 'SWING', 'GRID', 'HYBRID'];

export function Settings() {
  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
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
    try {
      await updateConfig(config);
      setMessage({ type: 'success', text: 'Configuration saved' });
      setEditing(false);
      try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success'); } catch {}
    } catch (err) {
      setMessage({ type: 'error', text: 'Failed to save configuration' });
      try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error'); } catch {}
    } finally {
      setSaving(false);
      setTimeout(() => setMessage(null), 3000);
    }
  };

  const updateField = (key: string, value: any) => {
    if (!config) return;
    setConfig({ ...config, [key]: value });
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
          onClick={() => setEditing(!editing)}
          className={`text-xs px-3 py-1.5 rounded-lg ${
            editing ? 'bg-amber-500/20 text-amber-400' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          {editing ? 'Cancel' : 'Edit'}
        </button>
      </div>

      {message && (
        <div className={`text-sm px-3 py-2 rounded-lg ${
          message.type === 'success' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        }`}>
          {message.text}
        </div>
      )}

      {/* Strategy */}
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-tg-hint">Strategy</h3>
        <div className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-3">
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
            <span className="text-sm text-tg-hint">{preset.leverage || 'N/A'}x</span>
          </div>
        </div>
      </section>

      {/* Position */}
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-tg-hint">Position</h3>
        <div className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-3">
          <SettingRow label="Position Size %" value={config.POSITION_SIZE_PERCENT} editing={editing} onChange={(v) => updateField('POSITION_SIZE_PERCENT', Number(v))} />
          <SettingRow label="Min Trade (USDT)" value={config.MIN_TRADE_AMOUNT_USDT} editing={editing} onChange={(v) => updateField('MIN_TRADE_AMOUNT_USDT', Number(v))} />
          <SettingRow label="Min Confidence" value={config.MIN_CONFIDENCE_THRESHOLD} editing={editing} onChange={(v) => updateField('MIN_CONFIDENCE_THRESHOLD', Number(v))} />
          <SettingRow label="Min R/R Ratio" value={config.MIN_RISK_REWARD_RATIO} editing={editing} onChange={(v) => updateField('MIN_RISK_REWARD_RATIO', Number(v))} />
        </div>
      </section>

      {/* AI */}
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-tg-hint">AI Settings</h3>
        <div className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-tg-text">Model</span>
            <span className="text-sm text-tg-hint">{config.AI_SETTINGS?.model || 'N/A'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-tg-text">Temperature</span>
            <span className="text-sm text-tg-hint">{config.AI_SETTINGS?.temperature ?? 'N/A'}</span>
          </div>
        </div>
      </section>

      {/* Symbols */}
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-tg-hint">Symbols</h3>
        <div className="bg-tg-section-bg rounded-xl p-3">
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(config.EXCHANGE_SYMBOLS || {}).map(([exchange, syms]) =>
              (syms as string[]).map((s) => (
                <span key={`${exchange}-${s}`} className="text-xs bg-tg-bg px-2 py-1 rounded text-tg-text">
                  {s}
                </span>
              ))
            )}
          </div>
        </div>
      </section>

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

function SettingRow({ label, value, editing, onChange }: {
  label: string;
  value: any;
  editing: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-tg-text">{label}</span>
      {editing ? (
        <input
          type="number"
          step="any"
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 w-24 text-right border border-white/10"
        />
      ) : (
        <span className="text-sm text-tg-hint">{value ?? 'N/A'}</span>
      )}
    </div>
  );
}
