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
  getBaseConfig,
  updateBaseConfig,
  addSymbol,
  removeSymbol,
  deleteProfile,
  cloneProfile,
  getProfileUsage,
  autoCreateProfile,
  updateProfile,
  type ConfigSystemInfo,
  type ActiveConfig,
  type TradingConfig,
  type BaseConfig,
  type StrategiesResponse,
  type ProfilesResponse,
  type SymbolProfilesResponse,
} from '../api/client';
import { ProfileCard } from '../components/ProfileCard';
import { Spinner } from '../components/Spinner';

type Tab = 'strategy' | 'trading' | 'infrastructure' | 'profiles' | 'symbols';

type Message = { type: 'success' | 'error' | 'warning'; text: string } | null;

const sortedUnique = (arr: string[]) => [...new Set(arr)].sort();

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

  // Base (Infrastructure) config
  const [baseConfig, setBaseConfig] = useState<BaseConfig | null>(null);

  // Strategies
  const [strategies, setStrategies] = useState<StrategiesResponse | null>(null);

  // Profiles
  const [profiles, setProfiles] = useState<ProfilesResponse | null>(null);

  // Symbol profiles
  const [symbolProfiles, setSymbolProfiles] = useState<SymbolProfilesResponse | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      setError(null);
      const [sysInfo, active, trading, strats, profs, symProfs, base] = await Promise.all([
        getConfigSystemInfo(),
        getActiveConfig(),
        getTradingConfig(),
        getStrategies(),
        getProfiles(),
        getSymbolProfiles(),
        getBaseConfig(),
      ]);
      // Debug logging for strategy loading issues
      console.log('[Settings] Config system info:', sysInfo);
      console.log('[Settings] Strategies response:', strats);
      console.log('[Settings] Available strategies:', strats?.available);
      console.log('[Settings] Active strategy:', active?.strategy);

      setSystemInfo(sysInfo);
      setActiveConfig(active);
      setTradingConfig(trading);
      setStrategies(strats);
      setProfiles(profs);
      setSymbolProfiles(symProfs);
      setBaseConfig(base);
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
      <div className="flex gap-1 bg-tg-section-bg p-1 rounded-xl overflow-x-auto no-scrollbar">
        {(['strategy', 'trading', 'infrastructure', 'profiles', 'symbols'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 text-xs py-2 px-2 rounded-lg transition-colors capitalize ${
              tab === t ? 'bg-tg-button text-white' : 'text-tg-hint hover:text-tg-text'
            }`}
          >
            {t === 'strategy' ? 'Strategy' : t === 'trading' ? 'Position & Risk' : t === 'infrastructure' ? 'AI Settings' : t === 'profiles' ? 'Profiles' : 'Symbols'}
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

      {tab === 'infrastructure' && baseConfig && strategies && (
        <InfrastructureTab
          config={baseConfig}
          strategies={strategies}
          onUpdate={async (data) => {
            try {
              const result = await updateBaseConfig(data);
              setBaseConfig(result.config);
              showMessage({ type: 'success', text: 'Infrastructure settings saved' });
              hapticFeedback('success');
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to save infrastructure' });
              hapticFeedback('error');
            }
          }}
        />
      )}

      {tab === 'profiles' && profiles && (
        <ProfilesTab profiles={profiles} onRefresh={fetchAll} />
      )}

      {tab === 'symbols' && symbolProfiles && profiles && (
        <SymbolsTab
          symbolProfiles={symbolProfiles}
          availableProfiles={profiles.available}
          onProfileUpdate={async (symbol, profile) => {
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
          onAddSymbol={async (symbol) => {
            try {
              await addSymbol(symbol);
              setSymbolProfiles({
                ...symbolProfiles,
                symbols: sortedUnique([...symbolProfiles.symbols, (symbol as string).toUpperCase()]),
              });
              showMessage({ type: 'success', text: `Symbol ${symbol} added` });
              hapticFeedback('success');
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to add symbol' });
              hapticFeedback('error');
            }
          }}
          onRemoveSymbol={async (symbol) => {
            if (!confirm(`Remove ${symbol}?`)) return;
            try {
              await removeSymbol(symbol);
              setSymbolProfiles({
                ...symbolProfiles,
                symbols: symbolProfiles.symbols.filter(s => s !== symbol),
              });
              showMessage({ type: 'success', text: `Symbol ${symbol} removed` });
              hapticFeedback('success');
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to remove symbol' });
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
  const [createProfileModal, setCreateProfileModal] = useState<{ open: boolean; strategy: string }>({ open: false, strategy: '' });
  const [profileName, setProfileName] = useState('');
  const [creatingProfile, setCreatingProfile] = useState(false);
  const [createProfileError, setCreateProfileError] = useState<string | null>(null);

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

  const handleCreateProfile = async () => {
    if (!profileName.trim()) return;
    setCreatingProfile(true);
    setCreateProfileError(null);
    try {
      const strategySettings = strategies.strategies[createProfileModal.strategy];
      await autoCreateProfile({
        name: profileName.trim(),
        strategy: createProfileModal.strategy,
        settings: {
          preset: strategySettings?.preset || {},
        },
        switch_from_default: true,
      });
      setCreateProfileModal({ open: false, strategy: '' });
      setProfileName('');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create profile';
      setCreateProfileError(message);
      console.error('Failed to create profile:', err);
    } finally {
      setCreatingProfile(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Trading Style Selector */}
      <Section title="Trading Style" badge="hot-reload">
        {strategies.available.length === 0 ? (
          <div className="text-sm text-amber-400 p-3 bg-amber-500/10 rounded-lg">
            No strategies found. Check if config/ directory is mounted correctly.
            <br />
            <span className="text-xs text-tg-hint">
              Expected: SCALP, AISCALP, SWING, GRID, HYBRID, MACDX
            </span>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {/* Dropdown Selector (Alternative) */}
            {/* <div className="flex flex-col gap-1.5">
              <span className="text-[10px] text-tg-hint uppercase ml-1">Select Style</span>
              <select
                value={selectedStrategy}
                onChange={(e) => setSelectedStrategy(e.target.value)}
                className="w-full bg-tg-bg text-tg-text text-sm rounded-xl px-4 py-3 border border-white/10 outline-none focus:border-tg-button transition-colors appearance-none"
                style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' fill=\'none\' viewBox=\'0 0 24 24\' stroke=\'%23777\'%3E%3Cpath stroke-linecap=\'round\' stroke-linejoin=\'round\' stroke-width=\'2\' d=\'M19 9l-7 7-7-7\'/%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 12px center', backgroundSize: '16px' }}
              >
                {strategies.available.map((name) => (
                  <option key={name} value={name}>
                    {name} {name === activeConfig.strategy ? '(Active)' : ''}
                  </option>
                ))}
              </select>
            </div> */}

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

      {/* Create Profile Button */}
      <button
        onClick={() => setCreateProfileModal({ open: true, strategy: selectedStrategy })}
        className="w-full py-3 bg-tg-section-bg text-tg-text font-medium rounded-xl border border-white/10 hover:border-white/20 transition-colors"
      >
        + Create Profile from {selectedStrategy}
      </button>

      {/* Create Profile Modal */}
      {createProfileModal.open && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-tg-section-bg rounded-xl p-4 w-[280px] shadow-xl">
            <h3 className="text-sm font-medium text-tg-text mb-3">Create Profile</h3>
            <p className="text-xs text-tg-hint mb-3">
              Create a profile based on {createProfileModal.strategy} strategy.
              <br />
              <span className="text-amber-400">Symbols using "default" will be switched to this new profile.</span>
            </p>
            <input
              type="text"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder="Enter profile name (optional)"
              className="w-full px-3 py-2 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-sm mb-2 focus:outline-none focus:border-tg-button"
              autoFocus
              onKeyDown={(e) => e.key === 'Enter' && handleCreateProfile()}
            />
            {createProfileError && (
              <p className="text-xs text-red-400 mb-3">{createProfileError}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => setCreateProfileModal({ open: false, strategy: '' })}
                className="flex-1 py-2 rounded-lg bg-tg-bg text-tg-text text-sm font-medium hover:bg-white/10"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateProfile}
                disabled={creatingProfile}
                className="flex-1 py-2 rounded-lg bg-tg-button text-white text-sm font-medium hover:bg-tg-button/80 disabled:opacity-50"
              >
                {creatingProfile ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
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
// Infrastructure Tab
// ============================================================================

function InfrastructureTab({
  config,
  strategies,
  onUpdate,
}: {
  config: BaseConfig;
  strategies?: StrategiesResponse;
  onUpdate: (data: Partial<BaseConfig>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [localConfig, setLocalConfig] = useState(config);
  const [saving, setSaving] = useState(false);
  const [createProfileModal, setCreateProfileModal] = useState(false);
  const [profileName, setProfileName] = useState('');
  const [selectedStrategy, setSelectedStrategy] = useState<string>('');
  const [creatingProfile, setCreatingProfile] = useState(false);
  const [createProfileError, setCreateProfileError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(localConfig);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const handleCreateProfile = async () => {
    if (!profileName.trim()) return;
    setCreatingProfile(true);
    setCreateProfileError(null);
    try {
      const strategySettings = strategies?.strategies[selectedStrategy];
      await autoCreateProfile({
        name: profileName.trim(),
        strategy: selectedStrategy,
        settings: {
          preset: strategySettings?.preset || {},
        },
        switch_from_default: true,
      });
      setCreateProfileModal(false);
      setProfileName('');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create profile';
      setCreateProfileError(message);
      console.error('Failed to create profile:', err);
    } finally {
      setCreatingProfile(false);
    }
  };

  const openCreateProfileModal = () => {
    setSelectedStrategy(strategies?.available[0] || '');
    setProfileName('');
    setCreateProfileError(null);
    setCreateProfileModal(true);
  };

  const updateAI = (key: string, value: any) => {
    setLocalConfig({
      ...localConfig,
      ai: { ...localConfig.ai, [key]: value },
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <button
          onClick={() => {
            if (editing) setLocalConfig(config);
            setEditing(!editing);
          }}
          className={`text-xs px-3 py-1.5 rounded-lg ${
            editing ? 'bg-amber-500/20 text-amber-400' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          {editing ? 'Cancel' : 'Edit'}
        </button>
      </div>

      <Section title="AI Infrastructure" badge="restart">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-tg-text">Provider</span>
            {editing ? (
              <select
                value={localConfig.ai?.provider || 'openai'}
                onChange={(e) => updateAI('provider', e.target.value)}
                className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 border border-white/10"
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="google">Google</option>
                <option value="deepseek">DeepSeek</option>
                <option value="openrouter">OpenRouter</option>
              </select>
            ) : (
              <span className="text-sm text-tg-hint capitalize">{localConfig.ai?.provider || 'openai'}</span>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-sm text-tg-text">Model</span>
            {editing ? (
              <input
                type="text"
                value={localConfig.ai?.model || ''}
                onChange={(e) => updateAI('model', e.target.value)}
                className="bg-tg-bg text-tg-text text-sm rounded px-3 py-1.5 border border-white/10 w-full"
                placeholder="e.g. gpt-4o"
              />
            ) : (
              <span className="text-xs text-tg-hint font-mono bg-tg-bg p-1.5 rounded">{localConfig.ai?.model || 'N/A'}</span>
            )}
          </div>

          <SettingRow
            label="Temperature"
            value={localConfig.ai?.temperature}
            editing={editing}
            step={0.1}
            onChange={(v) => updateAI('temperature', v)}
          />

          <SettingRow
            label="Max Tokens"
            value={localConfig.ai?.max_tokens}
            editing={editing}
            step={100}
            onChange={(v) => updateAI('max_tokens', v)}
          />
        </div>
      </Section>

      <Section title="AI Reasoning" badge="restart">
        <ToggleRow
          label="Enable Reasoning"
          value={localConfig.ai?.reasoning?.enabled ?? false}
          editing={editing}
          onChange={(v) => {
            const reasoning = { ...(localConfig.ai?.reasoning || { effort: 'medium', exclude: false }), enabled: v };
            updateAI('reasoning', reasoning);
          }}
        />
        {localConfig.ai?.reasoning?.enabled && (
          <div className="flex items-center justify-between mt-1">
            <span className="text-sm text-tg-text pl-4">Effort level</span>
            {editing ? (
              <select
                value={localConfig.ai?.reasoning?.effort || 'medium'}
                onChange={(e) => {
                  const reasoning = { ...(localConfig.ai?.reasoning || { enabled: true, exclude: false }), effort: e.target.value };
                  updateAI('reasoning', reasoning);
                }}
                className="bg-tg-bg text-tg-text text-sm rounded px-2 py-1 border border-white/10"
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            ) : (
              <span className="text-sm text-tg-hint uppercase">{localConfig.ai?.reasoning?.effort || 'medium'}</span>
            )}
          </div>
        )}
      </Section>

      {editing && (
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full py-3 bg-tg-button text-white font-medium rounded-xl disabled:opacity-50 transition-opacity"
        >
          {saving ? 'Saving...' : 'Save Infrastructure Settings'}
        </button>
      )}

      {/* Create Profile Button */}
      <button
        onClick={openCreateProfileModal}
        className="w-full py-3 bg-tg-section-bg text-tg-text font-medium rounded-xl border border-white/10 hover:border-white/20 transition-colors"
      >
        + Create Profile
      </button>

      {/* Create Profile Modal */}
      {createProfileModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-tg-section-bg rounded-xl p-4 w-[280px] shadow-xl">
            <h3 className="text-sm font-medium text-tg-text mb-3">Create Profile</h3>
            <p className="text-xs text-tg-hint mb-3">
              Create a new profile for symbol configuration.
              <br />
              <span className="text-amber-400">Symbols using "default" will be switched to this new profile.</span>
            </p>

            {/* Strategy Selector */}
            {strategies && (
              <div className="mb-3">
                <label className="text-xs text-tg-hint mb-1 block">Strategy</label>
                <select
                  value={selectedStrategy}
                  onChange={(e) => setSelectedStrategy(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-sm focus:outline-none focus:border-tg-button"
                >
                  {strategies.available.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </div>
            )}

            <input
              type="text"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder="Enter profile name (optional)"
              className="w-full px-3 py-2 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-sm mb-2 focus:outline-none focus:border-tg-button"
              autoFocus
              onKeyDown={(e) => e.key === 'Enter' && handleCreateProfile()}
            />
            {createProfileError && (
              <p className="text-xs text-red-400 mb-3">{createProfileError}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => {
                  setCreateProfileModal(false);
                  setCreateProfileError(null);
                }}
                className="flex-1 py-2 rounded-lg bg-tg-bg text-tg-text text-sm font-medium hover:bg-white/10"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateProfile}
                disabled={creatingProfile}
                className="flex-1 py-2 rounded-lg bg-tg-button text-white text-sm font-medium hover:bg-tg-button/80 disabled:opacity-50"
              >
                {creatingProfile ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Profiles Tab
// ============================================================================

function ProfilesTab({
  profiles,
  onRefresh,
}: {
  profiles: ProfilesResponse;
  onRefresh: () => void;
}) {
  const [profileUsages, setProfileUsages] = useState<Record<string, { isUsed: boolean; usageCount: number }>>({});
  const [cloneModal, setCloneModal] = useState<{ open: boolean; sourceName: string }>({ open: false, sourceName: '' });
  const [cloneName, setCloneName] = useState('');
  const [cloneLoading, setCloneLoading] = useState(false);
  const [editModal, setEditModal] = useState<{ open: boolean; name: string; profile: any }>({ open: false, name: '', profile: null });
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [profileFilter, setProfileFilter] = useState<string>('all');

  // Load profile usages in parallel
  useEffect(() => {
    const loadUsages = async () => {
      const profileNames = profiles.available.filter(name => name !== 'default');

      const promises = profileNames.map(async (name): Promise<[string, { isUsed: boolean; usageCount: number }]> => {
        try {
          const usage = await getProfileUsage(name);
          return [name, { isUsed: usage.isUsed, usageCount: usage.usageCount }];
        } catch {
          return [name, { isUsed: false, usageCount: 0 }];
        }
      });

      const results = await Promise.all(promises);
      setProfileUsages(Object.fromEntries(results));
    };
    loadUsages();
  }, [profiles.available]);

  const handleEdit = async (name: string) => {
    const profile = profiles.profiles[name];
    setEditError(null);
    setEditModal({ open: true, name, profile: profile || null });
  };

  const handleEditSave = async () => {
    if (!editModal.name || !editModal.profile) return;
    setEditLoading(true);
    setEditError(null);
    try {
      // Ensure _description is set for the backend
      const profileData = {
        ...editModal.profile,
        _description: editModal.profile._description || '',
      };
      await updateProfile(editModal.name, profileData as unknown as Record<string, unknown>);
      setEditModal({ open: false, name: '', profile: null });
      onRefresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update profile';
      setEditError(message);
      console.error('Failed to update profile:', err);
    } finally {
      setEditLoading(false);
    }
  };

  const handleClone = async (name: string) => {
    setCloneModal({ open: true, sourceName: name });
    setCloneName('');
  };

  const handleCloneSubmit = async () => {
    if (!cloneName.trim()) return;
    setCloneLoading(true);
    try {
      await cloneProfile(cloneModal.sourceName, cloneName.trim());
      setCloneModal({ open: false, sourceName: '' });
      onRefresh();
    } catch (err) {
      console.error('Failed to clone profile:', err);
    } finally {
      setCloneLoading(false);
    }
  };

  const handleDelete = async (name: string) => {
    const usage = profileUsages[name];
    if (usage?.isUsed) {
      alert('Cannot delete: profile is in use by symbols');
      return;
    }

    if (!confirm(`Delete profile "${name}"?`)) return;

    try {
      await deleteProfile(name);
      onRefresh();
    } catch (err) {
      console.error('Failed to delete profile:', err);
    }
  };

  // Group profiles by strategy
  const profilesByStrategy = profiles.available.reduce<Record<string, string[]>>((acc, name) => {
    const profile = profiles.profiles[name];
    const strategy = profile?._strategy || 'Other';
    if (!acc[strategy]) acc[strategy] = [];
    acc[strategy].push(name);
    return acc;
  }, {});

  const strategyGroups = Object.entries(profilesByStrategy).sort(([a], [b]) => {
    // Put 'default' first, then alphabetical
    if (a === 'default') return -1;
    if (b === 'default') return 1;
    return a.localeCompare(b);
  });

  return (
    <div className="flex flex-col gap-4">
      {/* Profile Filter Sub-tabs */}
      <div className="flex gap-1 bg-tg-section-bg p-1 rounded-xl overflow-x-auto no-scrollbar">
        <button
          onClick={() => setProfileFilter('all')}
          className={`text-xs py-1.5 px-3 rounded-lg transition-colors whitespace-nowrap ${
            profileFilter === 'all' ? 'bg-tg-button text-white' : 'text-tg-hint hover:text-tg-text'
          }`}
        >
          All ({profiles.available.length})
        </button>
        {strategyGroups.map(([strategy, names]) => (
          <button
            key={strategy}
            onClick={() => setProfileFilter(strategy)}
            className={`text-xs py-1.5 px-3 rounded-lg transition-colors whitespace-nowrap ${
              profileFilter === strategy ? 'bg-tg-button text-white' : 'text-tg-hint hover:text-tg-text'
            }`}
          >
            {strategy === 'Other' ? 'Other' : strategy} ({names.length})
          </button>
        ))}
      </div>

      {/* Profile Sections */}
      {(profileFilter === 'all' ? strategyGroups : strategyGroups.filter(([s]) => s === profileFilter)).map(([strategy, names]) => (
        <Section key={strategy} title={strategy === 'Other' ? 'Other Profiles' : `${strategy} Profiles`}>
          <div className="flex flex-col gap-2">
            {names.map((name) => {
              const profile = profiles.profiles[name];
              const usage = profileUsages[name];
              return (
                <ProfileCard
                  key={name}
                  name={name}
                  profile={profile}
                  isUsed={usage?.isUsed ?? false}
                  usageCount={usage?.usageCount ?? 0}
                  onEdit={() => handleEdit(name)}
                  onClone={() => handleClone(name)}
                  onDelete={() => handleDelete(name)}
                />
              );
            })}
          </div>
        </Section>
      ))}

      {/* Info */}
      <div className="text-xs text-tg-hint px-3 py-2 rounded-lg bg-tg-section-bg">
        Profiles allow per-symbol configuration overrides. Assign profiles to symbols in the Symbols tab.
      </div>

      {/* Clone Profile Modal */}
      {cloneModal.open && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-tg-section-bg rounded-xl p-4 w-[280px] shadow-xl">
            <h3 className="text-sm font-medium text-tg-text mb-3">Clone Profile</h3>
            <p className="text-xs text-tg-hint mb-3">
              Clone "{cloneModal.sourceName}" to a new name:
            </p>
            <input
              type="text"
              value={cloneName}
              onChange={(e) => setCloneName(e.target.value)}
              placeholder="Enter new profile name"
              className="w-full px-3 py-2 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-sm mb-3 focus:outline-none focus:border-tg-button"
              autoFocus
              onKeyDown={(e) => e.key === 'Enter' && handleCloneSubmit()}
            />
            <div className="flex gap-2">
              <button
                onClick={() => setCloneModal({ open: false, sourceName: '' })}
                className="flex-1 py-2 rounded-lg bg-tg-bg text-tg-text text-sm font-medium hover:bg-white/10"
              >
                Cancel
              </button>
              <button
                onClick={handleCloneSubmit}
                disabled={!cloneName.trim() || cloneLoading}
                className="flex-1 py-2 rounded-lg bg-tg-button text-white text-sm font-medium hover:bg-tg-button/80 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {cloneLoading ? 'Cloning...' : 'Clone'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Profile Modal */}
      {editModal.open && editModal.profile && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-tg-section-bg rounded-xl p-4 w-[300px] max-h-[80vh] overflow-y-auto shadow-xl">
            <h3 className="text-sm font-medium text-tg-text mb-3">Edit Profile: {editModal.name}</h3>

            {/* Description */}
            <div className="mb-3">
              <label className="text-xs text-tg-hint mb-1 block">Description</label>
              <input
                type="text"
                value={editModal.profile._description || editModal.profile.description || ''}
                onChange={(e) => setEditModal({
                  ...editModal,
                  profile: { ...editModal.profile, _description: e.target.value }
                })}
                className="w-full px-3 py-2 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-sm focus:outline-none focus:border-tg-button"
              />
            </div>

            {/* Preset Settings */}
            {editModal.profile.preset && Object.keys(editModal.profile.preset).length > 0 && (
              <div className="mb-3">
                <div className="text-xs text-tg-hint mb-2 uppercase">Preset Settings</div>
                {Object.entries(editModal.profile.preset).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-2 mb-2">
                    <span className="text-xs text-tg-text w-24 truncate">{key}</span>
                    <input
                      type="text"
                      value={String(value)}
                      onChange={(e) => setEditModal({
                        ...editModal,
                        profile: {
                          ...editModal.profile,
                          preset: { ...editModal.profile.preset, [key]: e.target.value }
                        }
                      })}
                      className="flex-1 px-2 py-1 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-xs focus:outline-none focus:border-tg-button"
                    />
                  </div>
                ))}
              </div>
            )}

            {/* Position Settings */}
            {editModal.profile.position && Object.keys(editModal.profile.position).length > 0 && (
              <div className="mb-3">
                <div className="text-xs text-tg-hint mb-2 uppercase">Position Settings</div>
                {Object.entries(editModal.profile.position).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-2 mb-2">
                    <span className="text-xs text-tg-text w-24 truncate">{key}</span>
                    <input
                      type="text"
                      value={String(value)}
                      onChange={(e) => setEditModal({
                        ...editModal,
                        profile: {
                          ...editModal.profile,
                          position: { ...editModal.profile.position, [key]: e.target.value }
                        }
                      })}
                      className="flex-1 px-2 py-1 rounded-lg bg-tg-bg border border-white/10 text-tg-text text-xs focus:outline-none focus:border-tg-button"
                    />
                  </div>
                ))}
              </div>
            )}

            {editError && (
              <p className="text-xs text-red-400 mb-3">{editError}</p>
            )}

            <div className="flex gap-2 mt-4">
              <button
                onClick={() => {
                  setEditModal({ open: false, name: '', profile: null });
                  setEditError(null);
                }}
                className="flex-1 py-2 rounded-lg bg-tg-bg text-tg-text text-sm font-medium hover:bg-white/10"
              >
                Cancel
              </button>
              <button
                onClick={handleEditSave}
                disabled={editLoading}
                className="flex-1 py-2 rounded-lg bg-tg-button text-white text-sm font-medium hover:bg-tg-button/80 disabled:opacity-50"
              >
                {editLoading ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Symbols Tab
// ============================================================================

function SymbolsTab({
  symbolProfiles,
  availableProfiles,
  onProfileUpdate,
  onAddSymbol,
  onRemoveSymbol,
}: {
  symbolProfiles: SymbolProfilesResponse;
  availableProfiles: string[];
  onProfileUpdate: (symbol: string, profile: string) => Promise<void>;
  onAddSymbol: (symbol: string) => Promise<void>;
  onRemoveSymbol: (symbol: string) => Promise<void>;
}) {
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [adding, setAdding] = useState(false);

  const handleProfileChange = async (symbol: string, profile: string) => {
    setSavingSymbol(symbol);
    try {
      await onProfileUpdate(symbol, profile);
    } finally {
      setSavingSymbol(null);
    }
  };

  const handleAddSymbol = async () => {
    if (!newSymbol.trim()) return;
    setAdding(true);
    try {
      await onAddSymbol(newSymbol.trim());
      setNewSymbol('');
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Add Symbol */}
      <div className="flex gap-2">
        <input
          type="text"
          value={newSymbol}
          onChange={(e) => setNewSymbol(e.target.value)}
          placeholder="BTC-USDT"
          className="flex-1 bg-tg-section-bg text-tg-text text-sm rounded-xl px-4 py-3 border border-white/10 outline-none focus:border-tg-button transition-colors"
        />
        <button
          onClick={handleAddSymbol}
          disabled={adding || !newSymbol.trim()}
          className="px-6 py-3 bg-tg-button text-white text-sm font-medium rounded-xl disabled:opacity-50"
        >
          Add
        </button>
      </div>

      <Section title="Active Symbols" badge="hot-reload">
        <div className="flex flex-col gap-3">
          {symbolProfiles.symbols.length === 0 ? (
            <div className="text-center py-4 text-tg-hint text-sm">No active symbols</div>
          ) : (
            symbolProfiles.symbols.map((symbol) => {
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
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => onRemoveSymbol(symbol)}
                      className="text-red-400 p-1 hover:bg-red-500/10 rounded-lg transition-colors"
                      title="Remove from config"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/></svg>
                    </button>
                    <div className="flex flex-col">
                      <span className={`text-sm font-medium ${isDisabled ? 'text-red-400' : 'text-tg-text'}`}>
                        {symbol}
                      </span>
                      {isDisabled && (
                        <span className="text-[10px] text-red-400">disabled</span>
                      )}
                    </div>
                  </div>

                  <select
                    value={currentProfile}
                    onChange={(e) => handleProfileChange(symbol, e.target.value)}
                    disabled={isSaving}
                    className="bg-tg-section-bg text-tg-text text-xs rounded-lg px-2 py-1.5 border border-white/10 disabled:opacity-50"
                  >
                    {availableProfiles.map((profile) => (
                      <option key={profile} value={profile}>
                        {profile}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })
          )}
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
