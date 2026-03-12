import { useEffect, useState, useCallback } from 'react';
import {
  getConfigSystemInfo,
  getActiveConfig,
  updateActiveConfig,
  getTradingConfig,
  updateTradingConfig,
  getStrategies,
  getProfiles,
  getSymbolProfiles,
  setSymbolProfile,
  type ConfigSystemInfo,
  type ActiveConfig,
  type TradingConfig,
  type StrategiesResponse,
  type ProfilesResponse,
  type SymbolProfilesResponse,
} from '../api/client';
import { Spinner } from '../components/Spinner';

type Tab = 'strategy' | 'trading' | 'profiles' | 'symbols';

type Message = { type: 'success' | 'error' | 'warning'; text: string } | null;

export function Settings() {
  const [tab, setTab] = useState<Tab>('strategy');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<Message>(null);

  // Config system info
  const [systemInfo, setSystemInfo] = useState<ConfigSystemInfo | null>(null);

  // Active config
  const [activeConfig, setActiveConfig] = useState<ActiveConfig | null>(null);

  // Trading config
  const [tradingConfig, setTradingConfig] = useState<TradingConfig | null>(null);

  // Strategies
  const [strategies, setStrategies] = useState<StrategiesResponse | null>(null);

  // Profiles
  const [profiles, setProfiles] = useState<ProfilesResponse | null>(null);

  // Symbol profiles
  const [symbolProfiles, setSymbolProfiles] = useState<SymbolProfilesResponse | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      setError(null);
      const [sysInfo, active, trading, strats, profs, symProfs] = await Promise.all([
        getConfigSystemInfo(),
        getActiveConfig(),
        getTradingConfig(),
        getStrategies(),
        getProfiles(),
        getSymbolProfiles(),
      ]);
      // Debug logging for strategy loading issues
      console.log('[Settings] Config system info:', sysInfo);
      console.log('[Settings] Strategies response:', strats);
      console.log('[Settings] Available strategies:', strats?.available);

      setSystemInfo(sysInfo);
      setActiveConfig(active);
      setTradingConfig(trading);
      setStrategies(strats);
      setProfiles(profs);
      setSymbolProfiles(symProfs);
    } catch (err) {
      console.error('Settings fetch error:', err);
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const showMessage = (msg: Message) => {
    setMessage(msg);
    setTimeout(() => setMessage(null), 4000);
  };

  const hapticFeedback = (type: 'success' | 'error' | 'warning') => {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred(
        type === 'success' ? 'success' : type === 'warning' ? 'warning' : 'error'
      );
    } catch {}
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  if (error || !systemInfo) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <span className="text-sm text-red-400">{error || 'Failed to load settings'}</span>
        <button onClick={fetchAll} className="text-xs px-4 py-2 bg-tg-section-bg text-tg-hint rounded-lg">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4 pb-24">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold text-tg-text">Settings</span>
        <div className="flex items-center gap-2">
          {systemInfo.use_new_system ? (
            <span className="text-[10px] px-2 py-1 rounded bg-green-500/20 text-green-400">
              new config
            </span>
          ) : (
            <span className="text-[10px] px-2 py-1 rounded bg-amber-500/20 text-amber-400">
              legacy
            </span>
          )}
        </div>
      </div>

      {/* Message */}
      {message && (
        <div className={`text-sm px-3 py-2 rounded-lg animate-fade-in ${
          message.type === 'success' ? 'bg-green-500/20 text-green-400' :
          message.type === 'warning' ? 'bg-amber-500/20 text-amber-400' :
          'bg-red-500/20 text-red-400'
        }`}>
          {message.text}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-tg-section-bg p-1 rounded-xl">
        {(['strategy', 'trading', 'profiles', 'symbols'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 text-xs py-2 px-2 rounded-lg transition-colors capitalize ${
              tab === t ? 'bg-tg-button text-white' : 'text-tg-hint hover:text-tg-text'
            }`}
          >
            {t === 'strategy' ? 'Strategy' : t === 'trading' ? 'Trading' : t === 'profiles' ? 'Profiles' : 'Symbols'}
          </button>
        ))}
      </div>

      {/* Content */}
      {tab === 'strategy' && activeConfig && strategies && (
        <StrategyTab
          activeConfig={activeConfig}
          strategies={strategies}
          onUpdate={async (data) => {
            try {
              await updateActiveConfig(data);
              setActiveConfig({ ...activeConfig, ...data });
              showMessage({ type: 'success', text: 'Strategy updated' });
              hapticFeedback('success');
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to update strategy' });
              hapticFeedback('error');
            }
          }}
        />
      )}

      {tab === 'trading' && tradingConfig && (
        <TradingTab
          config={tradingConfig}
          onUpdate={async (data) => {
            try {
              const result = await updateTradingConfig(data);
              setTradingConfig(result.config);
              showMessage({ type: 'success', text: 'Trading settings saved' });
              hapticFeedback('success');
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to save settings' });
              hapticFeedback('error');
            }
          }}
        />
      )}

      {tab === 'profiles' && profiles && (
        <ProfilesTab profiles={profiles} />
      )}

      {tab === 'symbols' && symbolProfiles && profiles && (
        <SymbolsTab
          symbolProfiles={symbolProfiles}
          availableProfiles={profiles.available}
          onUpdate={async (symbol, profile) => {
            try {
              await setSymbolProfile(symbol, profile);
              setSymbolProfiles({
                ...symbolProfiles,
                symbol_profiles: { ...symbolProfiles.symbol_profiles, [symbol]: profile },
              });
              showMessage({ type: 'success', text: `${symbol} profile set to ${profile}` });
              hapticFeedback('success');
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to update profile' });
              hapticFeedback('error');
            }
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// Strategy Tab
// ============================================================================

function StrategyTab({
  activeConfig,
  strategies,
  onUpdate,
}: {
  activeConfig: ActiveConfig;
  strategies: StrategiesResponse;
  onUpdate: (data: Partial<ActiveConfig>) => Promise<void>;
}) {
  const [selectedStrategy, setSelectedStrategy] = useState(activeConfig.strategy);
  const [saving, setSaving] = useState(false);

  const currentStrategy = strategies.strategies[selectedStrategy];

  const handleSave = async () => {
    if (selectedStrategy === activeConfig.strategy) return;
    setSaving(true);
    try {
      await onUpdate({ strategy: selectedStrategy });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Strategy Selector */}
      <Section title="Active Strategy" badge="hot-reload">
        {strategies.available.length === 0 ? (
          <div className="text-sm text-amber-400 p-3 bg-amber-500/10 rounded-lg">
            No strategies found. Check if config/ directory is mounted correctly.
            <br />
            <span className="text-xs text-tg-hint">
              Expected: SCALP, AISCALP, SWING, GRID, HYBRID, MACDX
            </span>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {strategies.available.map((name) => {
              const strat = strategies.strategies[name];
              const isActive = name === selectedStrategy;
              return (
                <button
                  key={name}
                  onClick={() => setSelectedStrategy(name)}
                  className={`flex flex-col items-start p-3 rounded-xl border transition-all ${
                    isActive
                      ? 'border-tg-button bg-tg-button/10'
                      : 'border-white/10 bg-tg-bg hover:border-white/20'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-medium ${isActive ? 'text-tg-button' : 'text-tg-text'}`}>
                      {name}
                    </span>
                    {strat?.has_ai && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400">AI</span>
                    )}
                  </div>
                  <span className="text-[10px] text-tg-hint mt-1 text-left line-clamp-2">
                    {strat?.description || 'No description'}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      {/* Strategy Details */}
      {currentStrategy && (
        <Section title="Strategy Settings">
          <div className="flex flex-col gap-2">
            <InfoRow label="Timeframe" value={currentStrategy.preset.timeframe || 'N/A'} />
            <InfoRow label="Leverage" value={`${currentStrategy.preset.leverage || 'N/A'}x`} />
            <InfoRow label="Loop Interval" value={`${currentStrategy.preset.loop_interval || 'N/A'}s`} />
            <InfoRow label="ATR SL Mult" value={String(currentStrategy.preset.atr_sl_mult || 'N/A')} />
            <InfoRow label="ATR TP Mult" value={String(currentStrategy.preset.atr_tp_mult || 'N/A')} />
          </div>
        </Section>
      )}

      {/* Save Button */}
      {selectedStrategy !== activeConfig.strategy && (
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-3 bg-tg-button text-white font-medium rounded-xl disabled:opacity-50 transition-opacity"
        >
          {saving ? 'Saving...' : `Switch to ${selectedStrategy}`}
        </button>
      )}
    </div>
  );
}

// ============================================================================
// Trading Tab
// ============================================================================

function TradingTab({
  config,
  onUpdate,
}: {
  config: TradingConfig;
  onUpdate: (data: Partial<TradingConfig>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [localConfig, setLocalConfig] = useState(config);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(localConfig);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const updatePosition = (key: string, value: number) => {
    setLocalConfig({
      ...localConfig,
      position: { ...localConfig.position, [key]: value },
    });
  };

  const updateRisk = (key: string, value: number) => {
    setLocalConfig({
      ...localConfig,
      risk: { ...localConfig.risk, [key]: value },
    });
  };

  const updateFeature = (key: string, value: boolean) => {
    setLocalConfig({
      ...localConfig,
      features: { ...localConfig.features, [key]: value },
    });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Edit Toggle */}
      <div className="flex justify-end">
        <button
          onClick={() => {
            if (editing) {
              setLocalConfig(config); // Reset on cancel
            }
            setEditing(!editing);
          }}
          className={`text-xs px-3 py-1.5 rounded-lg ${
            editing ? 'bg-amber-500/20 text-amber-400' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          {editing ? 'Cancel' : 'Edit'}
        </button>
      </div>

      {/* Position Settings */}
      <Section title="Position" badge="hot-reload">
        <SettingRow
          label="Size %"
          value={localConfig.position?.size_percent}
          editing={editing}
          step={1}
          onChange={(v) => updatePosition('size_percent', v)}
        />
        <SettingRow
          label="Min Trade USDT"
          value={localConfig.position?.min_trade_amount_usdt}
          editing={editing}
          step={1}
          onChange={(v) => updatePosition('min_trade_amount_usdt', v)}
        />
      </Section>

      {/* Risk Settings */}
      <Section title="Risk" badge="hot-reload">
        <SettingRow
          label="Min Confidence"
          value={localConfig.risk?.min_confidence_threshold}
          editing={editing}
          step={0.05}
          onChange={(v) => updateRisk('min_confidence_threshold', v)}
        />
        <SettingRow
          label="Min R/R Ratio"
          value={localConfig.risk?.min_risk_reward_ratio}
          editing={editing}
          step={0.1}
          onChange={(v) => updateRisk('min_risk_reward_ratio', v)}
        />
        <SettingRow
          label="Take Profit %"
          value={localConfig.risk?.take_profit_percent}
          editing={editing}
          step={0.1}
          onChange={(v) => updateRisk('take_profit_percent', v)}
        />
        <SettingRow
          label="Stop Loss %"
          value={localConfig.risk?.stop_loss_percent}
          editing={editing}
          step={0.1}
          onChange={(v) => updateRisk('stop_loss_percent', v)}
        />
      </Section>

      {/* Features */}
      <Section title="Features" badge="hot-reload">
        <ToggleRow
          label="News Enabled"
          value={localConfig.features?.enable_news ?? false}
          editing={editing}
          onChange={(v) => updateFeature('enable_news', v)}
        />
        <ToggleRow
          label="Aggressive Mode"
          value={localConfig.features?.aggressive_mode ?? false}
          editing={editing}
          onChange={(v) => updateFeature('aggressive_mode', v)}
        />
        <ToggleRow
          label="AI Skip on RSI"
          value={localConfig.features?.enable_ai_skip_on_rsi ?? false}
          editing={editing}
          onChange={(v) => updateFeature('enable_ai_skip_on_rsi', v)}
        />
        <ToggleRow
          label="Low Volume Filter"
          value={localConfig.features?.enable_low_volume_filter ?? false}
          editing={editing}
          onChange={(v) => updateFeature('enable_low_volume_filter', v)}
        />
      </Section>

      {/* Save Button */}
      {editing && (
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-3 bg-tg-button text-white font-medium rounded-xl disabled:opacity-50 transition-opacity"
        >
          {saving ? 'Saving...' : 'Save Trading Settings'}
        </button>
      )}
    </div>
  );
}

// ============================================================================
// Profiles Tab
// ============================================================================

function ProfilesTab({
  profiles,
}: {
  profiles: ProfilesResponse;
}) {
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-4">
      <Section title="Available Profiles">
        <div className="flex flex-col gap-2">
          {profiles.available.map((name) => {
            const profile = profiles.profiles[name];
            const isSelected = name === selectedProfile;
            return (
              <button
                key={name}
                onClick={() => setSelectedProfile(isSelected ? null : name)}
                className={`flex flex-col items-start p-3 rounded-xl border transition-all ${
                  isSelected
                    ? 'border-tg-button bg-tg-button/10'
                    : 'border-white/10 bg-tg-bg hover:border-white/20'
                }`}
              >
                <div className="flex items-center justify-between w-full">
                  <span className={`text-sm font-medium ${isSelected ? 'text-tg-button' : 'text-tg-text'}`}>
                    {name}
                  </span>
                  {profile.inherits && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-hint">
                      {profile.inherits}
                    </span>
                  )}
                </div>
                <span className="text-[10px] text-tg-hint mt-1 text-left">
                  {profile.description || 'No description'}
                </span>

                {/* Expanded Details */}
                {isSelected && (
                  <div className="mt-3 pt-3 border-t border-white/10 w-full text-left">
                    {profile.preset && Object.keys(profile.preset).length > 0 && (
                      <div className="mb-2">
                        <span className="text-[10px] text-tg-hint uppercase">Preset</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {Object.entries(profile.preset).map(([k, v]) => (
                            <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-text">
                              {k}: {String(v)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {profile.position && Object.keys(profile.position).length > 0 && (
                      <div className="mb-2">
                        <span className="text-[10px] text-tg-hint uppercase">Position</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {Object.entries(profile.position).map(([k, v]) => (
                            <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-text">
                              {k}: {String(v)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {profile.signal_rules && Object.keys(profile.signal_rules).length > 0 && (
                      <div>
                        <span className="text-[10px] text-tg-hint uppercase">Signal Rules</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {Object.entries(profile.signal_rules).map(([k, v]) => (
                            <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-text">
                              {k}: {String(v)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </Section>

      {/* Info */}
      <div className="text-xs text-tg-hint px-3 py-2 rounded-lg bg-tg-section-bg">
        Profiles allow per-symbol configuration overrides. Assign profiles to symbols in the Symbols tab.
      </div>
    </div>
  );
}

// ============================================================================
// Symbols Tab
// ============================================================================

function SymbolsTab({
  symbolProfiles,
  availableProfiles,
  onUpdate,
}: {
  symbolProfiles: SymbolProfilesResponse;
  availableProfiles: string[];
  onUpdate: (symbol: string, profile: string) => Promise<void>;
}) {
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);

  const handleProfileChange = async (symbol: string, profile: string) => {
    setSavingSymbol(symbol);
    try {
      await onUpdate(symbol, profile);
    } finally {
      setSavingSymbol(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Section title="Symbol Profiles" badge="hot-reload">
        <div className="flex flex-col gap-3">
          {symbolProfiles.symbols.map((symbol) => {
            const currentProfile = symbolProfiles.symbol_profiles[symbol] || 'default';
            const isDisabled = symbolProfiles.disabled_symbols.includes(symbol);
            const isSaving = savingSymbol === symbol;

            return (
              <div
                key={symbol}
                className={`flex items-center justify-between p-3 rounded-xl border ${
                  isDisabled ? 'border-red-500/30 bg-red-500/5' : 'border-white/10 bg-tg-bg'
                }`}
              >
                <div className="flex flex-col">
                  <span className={`text-sm font-medium ${isDisabled ? 'text-red-400' : 'text-tg-text'}`}>
                    {symbol}
                  </span>
                  {isDisabled && (
                    <span className="text-[10px] text-red-400">disabled</span>
                  )}
                </div>

                <select
                  value={currentProfile}
                  onChange={(e) => handleProfileChange(symbol, e.target.value)}
                  disabled={isSaving}
                  className="bg-tg-section-bg text-tg-text text-sm rounded-lg px-2 py-1.5 border border-white/10 disabled:opacity-50"
                >
                  {availableProfiles.map((profile) => (
                    <option key={profile} value={profile}>
                      {profile}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Disabled symbols info */}
      {symbolProfiles.disabled_symbols.length > 0 && (
        <div className="text-xs text-amber-400 px-3 py-2 rounded-lg bg-amber-500/10">
          Disabled symbols will not open new positions but existing positions will be managed.
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Shared Components
// ============================================================================

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

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-tg-text">{label}</span>
      <span className="text-sm text-tg-hint">{value}</span>
    </div>
  );
}

function SettingRow({ label, value, editing, onChange, step = 1 }: {
  label: string;
  value: number | undefined;
  editing: boolean;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-tg-text">{label}</span>
      {editing ? (
        <input
          type="number"
          step={step}
          value={value ?? ''}
          onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
          className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 w-24 text-right border border-white/10"
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
