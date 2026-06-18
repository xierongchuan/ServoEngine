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
  createStrategyInstance,
  updateStrategyInstance,
  deleteStrategyInstance,
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
  type StrategyInstance,
} from '../api/client';
import { ProfileCard } from '../components/ProfileCard';
import { ProfileEditor } from '../components/ProfileEditor';
import { Spinner } from '../components/Spinner';
import { Button } from '../components/ui/Button';
import { Card as Section } from '../components/ui/Card';
import { ListRow as InfoRow, ListInputRow as SettingRow, ListToggleRow as ToggleRow } from '../components/ui/List';
import { Tabs } from '../components/ui/Tabs';

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
        <Button onClick={fetchAll} variant="secondary" size="sm">
          Retry
        </Button>
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
      <Tabs
        value={tab}
        onChange={(v) => setTab(v as Tab)}
        options={[
          { value: 'strategy', label: 'Strategy' },
          { value: 'trading', label: 'Position & Risk' },
          { value: 'infrastructure', label: 'AI Settings' },
          { value: 'profiles', label: 'Profiles' },
          { value: 'symbols', label: 'Symbols' },
        ]}
      />

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
          strategies={strategies}
          onRefresh={fetchAll}
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
  const hasStrategyInstances = (activeConfig.strategy_instances?.length || 0) > 0;
  const activeStrategies = sortedUnique((activeConfig.strategy_instances || [])
    .filter((instance) => instance.enabled)
    .map((instance) => instance.strategy));

  const handleSave = async () => {
    if (hasStrategyInstances) return;
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
      <Section title="Trading Style" badge={hasStrategyInstances ? 'instances' : 'hot-reload'}>
        {hasStrategyInstances && (
          <div className="text-xs text-tg-hint px-3 py-2 mb-3 rounded-lg bg-tg-bg border border-white/10">
            Active runtime uses {activeConfig.strategy_instances?.length || 0} strategy instances:
            {' '}
            <span className="text-tg-text">{activeStrategies.join(', ') || 'none'}</span>.
            Edit per-symbol strategy and profile in the Symbols tab.
          </div>
        )}
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
            <InfoRow label="SL %" value={String(currentStrategy.preset.sl_percent || 'N/A')} />
            <InfoRow label="TP %" value={String(currentStrategy.preset.tp_percent || 'N/A')} />
          </div>
        </Section>
      )}

      {/* Save Button */}
      {!hasStrategyInstances && selectedStrategy !== activeConfig.strategy && (
        <Button
          fullWidth
          onClick={handleSave}
          disabled={saving}
          isLoading={saving}
        >
          {saving ? 'Saving...' : `Switch to ${selectedStrategy}`}
        </Button>
      )}

      {/* Create Profile Button */}
      <Button
        fullWidth
        variant="outline"
        onClick={() => setCreateProfileModal({ open: true, strategy: selectedStrategy })}
      >
        + Create Profile from {selectedStrategy}
      </Button>

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
            <div className="flex gap-2 pt-2">
              <Button
                fullWidth
                variant="ghost"
                onClick={() => setCreateProfileModal({ open: false, strategy: '' })}
              >
                Cancel
              </Button>
              <Button
                fullWidth
                onClick={handleCreateProfile}
                disabled={creatingProfile}
                isLoading={creatingProfile}
              >
                Create
              </Button>
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
        <Button
          onClick={() => {
            if (editing) {
              setLocalConfig(config); // Reset on cancel
            }
            setEditing(!editing);
          }}
          variant={editing ? 'secondary' : 'ghost'}
          size="sm"
        >
          {editing ? 'Cancel' : 'Edit'}
        </Button>
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
        <Button
          fullWidth
          onClick={handleSave}
          disabled={saving}
          isLoading={saving}
        >
          Save Trading Settings
        </Button>
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
        <Button
          onClick={() => {
            if (editing) setLocalConfig(config);
            setEditing(!editing);
          }}
          variant={editing ? 'secondary' : 'ghost'}
          size="sm"
        >
          {editing ? 'Cancel' : 'Edit'}
        </Button>
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
        <Button
          fullWidth
          onClick={handleSave}
          disabled={saving}
          isLoading={saving}
        >
          Save Infrastructure Settings
        </Button>
      )}

      {/* Create Profile Button */}
      <Button
        fullWidth
        variant="outline"
        onClick={openCreateProfileModal}
      >
        + Create Profile
      </Button>

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
            <div className="flex gap-2 pt-2">
              <Button
                fullWidth
                variant="ghost"
                onClick={() => {
                  setCreateProfileModal(false);
                  setCreateProfileError(null);
                }}
              >
                Cancel
              </Button>
              <Button
                fullWidth
                onClick={handleCreateProfile}
                disabled={creatingProfile}
                isLoading={creatingProfile}
              >
                Create
              </Button>
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
    const profile = profiles.profiles[name] as Record<string, unknown> | undefined;
    const strategy = (profile?._strategy as string) || 'Other';
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
      <Tabs
        value={profileFilter}
        onChange={setProfileFilter}
        options={[
          { value: 'all', label: `All (${profiles.available.length})` },
          ...strategyGroups.map(([strategy, names]) => ({
            value: strategy,
            label: `${strategy === 'Other' ? 'Other' : strategy} (${names.length})`
          }))
        ]}
      />

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
            <div className="flex gap-2 pt-2">
              <Button
                fullWidth
                variant="ghost"
                onClick={() => setCloneModal({ open: false, sourceName: '' })}
              >
                Cancel
              </Button>
              <Button
                fullWidth
                onClick={handleCloneSubmit}
                disabled={!cloneName.trim()}
                isLoading={cloneLoading}
              >
                Clone
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Profile Modal - Using ProfileEditor Component */}
      {editModal.open && editModal.profile && (
        <ProfileEditor
          profile={editModal.profile}
          profileName={editModal.name}
          onSave={async (updatedProfile) => {
            setEditLoading(true);
            setEditError(null);
            try {
              await updateProfile(editModal.name, updatedProfile as unknown as Record<string, unknown>);
              setEditModal({ open: false, name: '', profile: null });
              onRefresh();
            } catch (err) {
              const message = err instanceof Error ? err.message : 'Failed to update profile';
              setEditError(message);
              console.error('Failed to update profile:', err);
            } finally {
              setEditLoading(false);
            }
          }}
          onCancel={() => {
            setEditModal({ open: false, name: '', profile: null });
            setEditError(null);
          }}
          isLoading={editLoading}
          error={editError}
        />
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
  strategies,
  onRefresh,
  onProfileUpdate,
  onAddSymbol,
  onRemoveSymbol,
}: {
  symbolProfiles: SymbolProfilesResponse;
  availableProfiles: string[];
  strategies: StrategiesResponse | null;
  onRefresh: () => void;
  onProfileUpdate: (symbol: string, profile: string) => Promise<void>;
  onAddSymbol: (symbol: string) => Promise<void>;
  onRemoveSymbol: (symbol: string) => Promise<void>;
}) {
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [newStrategy, setNewStrategy] = useState(strategies?.available[0] || 'HYBRID');
  const [newProfile, setNewProfile] = useState('default');
  const [adding, setAdding] = useState(false);
  const instances = symbolProfiles.strategy_instances || [];

  const handleProfileChange = async (symbol: string, profile: string, instanceId?: string) => {
    setSavingSymbol(instanceId || symbol);
    try {
      if (instanceId) {
        await setSymbolProfile(symbol, profile, instanceId);
        onRefresh();
      } else {
        await onProfileUpdate(symbol, profile);
      }
    } finally {
      setSavingSymbol(null);
    }
  };

  const handleAddInstance = async () => {
    if (!newSymbol.trim()) return;
    setAdding(true);
    try {
      if (strategies?.available?.length) {
        await createStrategyInstance({
          symbol: newSymbol.trim().toUpperCase(),
          strategy: newStrategy,
          profile: newProfile,
          enabled: true,
        });
        onRefresh();
      } else {
        await onAddSymbol(newSymbol.trim());
      }
      setNewSymbol('');
    } finally {
      setAdding(false);
    }
  };

  const handleUpdateInstance = async (instance: StrategyInstance, data: Partial<StrategyInstance>) => {
    setSavingSymbol(instance.id);
    try {
      await updateStrategyInstance(instance.id, data);
      onRefresh();
    } finally {
      setSavingSymbol(null);
    }
  };

  const handleDeleteInstance = async (instance: StrategyInstance) => {
    if (!confirm(`Remove ${instance.id}?`)) return;
    setSavingSymbol(instance.id);
    try {
      await deleteStrategyInstance(instance.id);
      onRefresh();
    } finally {
      setSavingSymbol(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Add Strategy Instance */}
      <div className="grid grid-cols-2 gap-2">
        <input
          type="text"
          value={newSymbol}
          onChange={(e) => setNewSymbol(e.target.value)}
          placeholder="BTCUSDT"
          className="tg-control text-sm rounded-xl px-4 py-3"
        />
        <select
          value={newStrategy}
          onChange={(e) => setNewStrategy(e.target.value)}
          className="tg-control text-sm rounded-xl px-3 py-3"
        >
          {(strategies?.available || ['HYBRID']).map((strategy) => (
            <option key={strategy} value={strategy}>{strategy}</option>
          ))}
        </select>
        <select
          value={newProfile}
          onChange={(e) => setNewProfile(e.target.value)}
          className="tg-control text-sm rounded-xl px-3 py-3"
        >
          {availableProfiles.map((profile) => (
            <option key={profile} value={profile}>{profile}</option>
          ))}
        </select>
        <Button
          variant="secondary"
          onClick={handleAddInstance}
          disabled={adding || !newSymbol.trim()}
        >
          Add Instance
        </Button>
      </div>

      {instances.length > 0 && (
        <Section title="Strategy Instances" badge="restart">
          <div className="flex flex-col gap-3">
            {instances.map((instance) => {
              const isSaving = savingSymbol === instance.id;
              const isDisabled = !instance.enabled || symbolProfiles.disabled_symbols.includes(instance.symbol);

              return (
                <div
                  key={instance.id}
                  className={`flex flex-col gap-3 p-3 rounded-xl border ${
                    isDisabled ? 'border-red-500/30 bg-red-500/5' : 'border-white/10 bg-tg-bg'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex flex-col min-w-0">
                      <span className={`text-sm font-medium ${isDisabled ? 'text-red-400' : 'text-tg-text'}`}>
                        {instance.symbol}
                      </span>
                      <span className="text-[10px] text-tg-hint truncate">{instance.id}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleUpdateInstance(instance, { enabled: !instance.enabled })}
                        disabled={isSaving}
                        className={`text-xs px-2 py-1 rounded-lg border ${
                          instance.enabled
                            ? 'border-green-500/30 text-green-400 bg-green-500/10'
                            : 'border-red-500/30 text-red-400 bg-red-500/10'
                        } disabled:opacity-50`}
                      >
                        {instance.enabled ? 'enabled' : 'disabled'}
                      </button>
                      <button
                        onClick={() => handleDeleteInstance(instance)}
                        disabled={isSaving}
                        className="text-red-400 p-1 hover:bg-red-500/10 rounded-lg transition-colors disabled:opacity-50"
                        title="Remove instance"
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/></svg>
                      </button>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={instance.strategy}
                      onChange={(e) => handleUpdateInstance(instance, { strategy: e.target.value })}
                      disabled={isSaving}
                      className="tg-control text-xs rounded-lg px-2 py-1.5"
                    >
                      {(strategies?.available || [instance.strategy]).map((strategy) => (
                        <option key={strategy} value={strategy}>{strategy}</option>
                      ))}
                    </select>

                    <select
                      value={instance.profile || 'default'}
                      onChange={(e) => handleProfileChange(instance.symbol, e.target.value, instance.id)}
                      disabled={isSaving}
                      className="tg-control text-xs rounded-lg px-2 py-1.5"
                    >
                      {availableProfiles.map((profile) => (
                        <option key={profile} value={profile}>{profile}</option>
                      ))}
                    </select>
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      <Section title={instances.length > 0 ? 'Legacy Symbols View' : 'Active Symbols'} badge="hot-reload">
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
                    className="tg-control max-w-40 text-xs rounded-lg px-2 py-1.5"
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

// Removed shared components
