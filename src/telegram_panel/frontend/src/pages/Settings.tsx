import { useEffect, useState, useCallback } from 'react';
import {
  getConfigSystemInfo,
  getTradingConfig,
  updateTradingConfig,
  getStrategies,
  getProfiles,
  getSymbolProfiles,
  setSymbolProfile,
  getBaseConfig,
  createStrategyInstance,
  updateStrategyInstance,
  deleteStrategyInstance,
  deleteProfile,
  cloneProfile,
  getProfileUsage,
  updateProfile,
  getActiveTrades,
  getRuntimeStatus,
  startRuntime,
  stopRuntime,
  restartRuntime,
  restartSymbolRuntime,
  restartSymbolRuntimeBatch,
  startSymbolRuntime,
  stopSymbolRuntime,
  type ConfigSystemInfo,
  type TradingConfig,
  type BaseConfig,
  type StrategiesResponse,
  type ProfilesResponse,
  type SymbolProfilesResponse,
  type StrategyInstance,
  type RuntimeStatus,
} from '../api/client';
import type { Trade } from '../api/types';
import { ProfileCard } from '../components/ProfileCard';
import { ProfileEditor } from '../components/ProfileEditor';
import { ExecutionSettings } from '../components/config/ExecutionSettings';
import { AISettings } from '../components/config/AISettings';
import { applyRuntimeConfigChange, canControlWorker } from '../components/config/runtimeActions';
import { Spinner } from '../components/Spinner';
import { Button } from '../components/ui/Button';
import { Card as Section } from '../components/ui/Card';
import { ListInputRow as SettingRow, ListToggleRow as ToggleRow } from '../components/ui/List';
import { StatusDot } from '../components/ui/StatusDot';
import { Tabs } from '../components/ui/Tabs';

type Tab = 'runtime' | 'trading' | 'profiles' | 'infrastructure' | 'ai';

type Message = { type: 'success' | 'error' | 'warning'; text: string } | null;

export function Settings() {
  const [tab, setTab] = useState<Tab>('runtime');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<Message>(null);

  // Config system info
  const [systemInfo, setSystemInfo] = useState<ConfigSystemInfo | null>(null);

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
  const [activeTrades, setActiveTrades] = useState<Trade[]>([]);

  const fetchAll = useCallback(async () => {
    try {
      setError(null);
      const [sysInfo, trading, strats, profs, symProfs, base, trades] = await Promise.all([
        getConfigSystemInfo(),
        getTradingConfig(),
        getStrategies(),
        getProfiles(),
        getSymbolProfiles(),
        getBaseConfig(),
        getActiveTrades(),
      ]);
      setSystemInfo(sysInfo);
      setTradingConfig(trading);
      setStrategies(strats);
      setProfiles(profs);
      setSymbolProfiles(symProfs);
      setBaseConfig(base);
      setActiveTrades(Array.isArray(trades) ? trades : Object.values(trades || {}) as Trade[]);
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

  const normalizeSymbol = (symbol: string) => symbol.replace(/[-/]/g, '').toUpperCase();

  const getRestartableInstances = () => {
    const disabled = new Set((symbolProfiles?.disabled_symbols || []).map(normalizeSymbol));
    return (symbolProfiles?.strategy_instances || []).filter(
      (instance) => instance.enabled && !disabled.has(normalizeSymbol(instance.symbol))
    );
  };

  const hasActivePosition = (symbol: string) => {
    const target = normalizeSymbol(symbol);
    return activeTrades.some((trade) => normalizeSymbol(trade.symbol) === target);
  };

  const confirmRestartForInstances = (instances: StrategyInstance[], reason: string) => {
    const activeSymbols = Array.from(
      new Set(instances.filter((instance) => hasActivePosition(instance.symbol)).map((instance) => instance.symbol))
    );
    if (activeSymbols.length === 0) return true;
    return confirm(
      `Изменение ${reason} затронет активные позиции: ${activeSymbols.join(', ')}. ` +
      'Сохранить конфиг и перезапустить процессы этих символов?'
    );
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
          { value: 'runtime', label: 'Runtime' },
          { value: 'trading', label: 'Исполнение и риск' },
          { value: 'profiles', label: 'Profiles' },
          { value: 'ai', label: 'ИИ' },
          { value: 'infrastructure', label: 'Инфраструктура' },
        ]}
      />

      {/* Content */}
      {tab === 'trading' && tradingConfig && (
        <>
        <ExecutionSettings />
        <TradingTab
          config={tradingConfig}
          onUpdate={async (data) => {
            try {
              const instancesToRestart = getRestartableInstances();
              if (instancesToRestart.length > 0 && !confirmRestartForInstances(instancesToRestart, 'Position & Risk')) {
                return;
              }
              const notice = await applyRuntimeConfigChange({
                save: () => updateTradingConfig(data),
                refresh: fetchAll,
                queue: instancesToRestart.length ? () => restartSymbolRuntimeBatch(
                  instancesToRestart.map((instance) => instance.id), 'trading_config_changed'
                ) : undefined,
              });
              showMessage(notice);
              hapticFeedback(notice.type);
            } catch (err) {
              showMessage({ type: 'error', text: 'Failed to save settings' });
              hapticFeedback('error');
            }
          }}
        />
        </>
      )}

      {tab === 'ai' && <AISettings />}

      {tab === 'infrastructure' && baseConfig && strategies && <InfrastructureTab />}

      {tab === 'profiles' && profiles && (
        <ProfilesTab
          profiles={profiles}
          symbolProfiles={symbolProfiles}
          activeTrades={activeTrades}
          onRefresh={fetchAll}
          onNotice={showMessage}
        />
      )}

      {tab === 'runtime' && symbolProfiles && profiles && strategies && (
        <RuntimeTab
          symbolProfiles={symbolProfiles}
          activeTrades={activeTrades}
          profiles={profiles}
          strategies={strategies}
          onRefresh={fetchAll}
          onSuccess={(text) => {
            showMessage({ type: 'success', text });
            hapticFeedback('success');
          }}
          onWarning={(text) => showMessage({ type: 'warning', text })}
          onError={(text) => {
            showMessage({ type: 'error', text });
            hapticFeedback('error');
          }}
        />
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
      // execution сохраняется отдельно, без автоматического перезапуска.
      await onUpdate({ position: localConfig.position, risk: localConfig.risk, features: localConfig.features });
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
          label="Новости (из конфигурации)"
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

function InfrastructureTab() {
  return (
    <Section title="Инфраструктура">
      <p className="text-sm leading-relaxed text-tg-hint">
        Провайдеры и модели перенесены в отдельную вкладку «ИИ».
        Ключи API задаются только в окружении контейнера, не в панели.
      </p>
    </Section>
  );
}

// ============================================================================
// Profiles Tab
// ============================================================================

function ProfilesTab({
  profiles,
  symbolProfiles,
  activeTrades,
  onRefresh,
  onNotice,
}: {
  profiles: ProfilesResponse;
  symbolProfiles: SymbolProfilesResponse | null;
  activeTrades: Trade[];
  onRefresh: () => void;
  onNotice: (notice: Message) => void;
}) {
  const [profileUsages, setProfileUsages] = useState<Record<string, { isUsed: boolean; usageCount: number }>>({});
  const [cloneModal, setCloneModal] = useState<{ open: boolean; sourceName: string }>({ open: false, sourceName: '' });
  const [cloneName, setCloneName] = useState('');
  const [cloneLoading, setCloneLoading] = useState(false);
  const [editModal, setEditModal] = useState<{ open: boolean; name: string; profile: any }>({ open: false, name: '', profile: null });
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [profileFilter, setProfileFilter] = useState<string>('all');

  const normalizeSymbol = (symbol: string) => symbol.replace(/[-/]/g, '').toUpperCase();

  const getInstancesUsingProfile = (profileName: string): StrategyInstance[] => {
    const disabled = new Set((symbolProfiles?.disabled_symbols || []).map(normalizeSymbol));
    return (symbolProfiles?.strategy_instances || []).filter(
      (instance) =>
        instance.profile === profileName &&
        instance.enabled &&
        !disabled.has(normalizeSymbol(instance.symbol))
    );
  };

  const confirmProfileRestart = (instances: StrategyInstance[], profileName: string): boolean => {
    const activeSymbols = Array.from(
      new Set(
        instances
          .filter((instance) =>
            activeTrades.some((trade) => normalizeSymbol(trade.symbol) === normalizeSymbol(instance.symbol))
          )
          .map((instance) => instance.symbol)
      )
    );
    if (activeSymbols.length === 0) return true;
    return confirm(
      `Профиль "${profileName}" используется активными позициями: ${activeSymbols.join(', ')}. ` +
      'Сохранить профиль и перезапустить процессы этих символов?'
    );
  };

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
      alert('Cannot delete: profile is in use by strategy instances');
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
        Profiles allow per-instance configuration overrides. Assign profiles to strategy instances in Runtime.
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
              className="tg-control px-3 py-2 rounded-lg text-sm mb-3"
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
              const affectedInstances = getInstancesUsingProfile(editModal.name);
              if (affectedInstances.length > 0 && !confirmProfileRestart(affectedInstances, editModal.name)) {
                return;
              }
              const notice = await applyRuntimeConfigChange({
                save: () => updateProfile(editModal.name, updatedProfile as unknown as Record<string, unknown>),
                refresh: onRefresh,
                queue: affectedInstances.length ? () => restartSymbolRuntimeBatch(
                  affectedInstances.map((instance) => instance.id), `profile_${editModal.name}_changed`
                ) : undefined,
              });
              setEditModal({ open: false, name: '', profile: null });
              onNotice(notice);
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
// Runtime Tab
// ============================================================================

type RuntimeAction = 'start' | 'stop' | 'restart';

function runtimeStateLabel(state: string): string {
  const labels: Record<string, string> = {
    running: 'Работает',
    stopped: 'Остановлен',
    starting: 'Запускается',
    stopping: 'Останавливается',
    restarting: 'Перезапуск',
    crashed: 'Ошибка',
    unavailable: 'Нет связи',
  };
  return labels[state] || state;
}

function formatRuntimeTime(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function runtimeActionLabel(action: RuntimeAction): string {
  const labels: Record<RuntimeAction, string> = {
    start: 'Старт',
    stop: 'Стоп',
    restart: 'Рестарт',
  };
  return labels[action];
}

function getRuntimeErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function RuntimeTab({
  symbolProfiles,
  activeTrades,
  profiles,
  strategies,
  onRefresh,
  onSuccess,
  onWarning,
  onError,
}: {
  symbolProfiles: SymbolProfilesResponse;
  activeTrades: Trade[];
  profiles: ProfilesResponse;
  strategies: StrategiesResponse | null;
  onRefresh: () => void;
  onSuccess: (text: string) => void;
  onWarning: (text: string) => void;
  onError: (text: string) => void;
}) {
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [newStrategy, setNewStrategy] = useState(strategies?.available[0] || 'HYBRID');
  const [newProfile, setNewProfile] = useState('default');
  const [adding, setAdding] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeAction, setRuntimeAction] = useState<RuntimeAction | null>(null);
  const instances = symbolProfiles.strategy_instances || [];
  const runtimeState = runtimeStatus?.state || 'unavailable';
  const runtimeBusy = runtimeAction !== null || ['starting', 'stopping', 'restarting'].includes(runtimeState);
  const runtimeControlEnabled = !!runtimeStatus?.control_enabled && !runtimeStatus?.stale;
  const runtimeRunning = ['running', 'starting', 'restarting'].includes(runtimeState);
  const runtimeStopped = runtimeState === 'stopped';
  const workerControlEnabled = canControlWorker(runtimeStatus) && !runtimeBusy;
  const reportChange = (notice: { type: 'success' | 'warning'; text: string }) => {
    if (notice.type === 'warning') onWarning(notice.text); else onSuccess(notice.text);
  };

  const getCompatibleProfiles = useCallback((strategy: string): string[] => {
    const strategyUpper = strategy.toUpperCase();
    const serverCompatible = profiles.compatible_by_strategy?.[strategyUpper];
    if (serverCompatible?.length) {
      return serverCompatible;
    }

    const compatible = profiles.available.filter((profileName) => {
      const profileStrategy = profiles.profile_strategies?.[profileName]
        ?? (profiles.profiles[profileName] as Record<string, unknown> | undefined)?._strategy;
      return !profileStrategy || String(profileStrategy).toUpperCase() === strategyUpper;
    });
    return compatible.length > 0 ? compatible : ['default'];
  }, [profiles]);

  const getFallbackProfile = useCallback((strategy: string, currentProfile: string): string => {
    const compatible = getCompatibleProfiles(strategy);
    if (compatible.includes(currentProfile)) return currentProfile;
    if (compatible.includes('default')) return 'default';
    return compatible[0];
  }, [getCompatibleProfiles]);

  const newCompatibleProfiles = getCompatibleProfiles(newStrategy);

  const normalizeSymbol = useCallback((symbol: string) => symbol.replace(/[-/]/g, '').toUpperCase(), []);

  const symbolHasPosition = useCallback((symbol: string): boolean => {
    const target = normalizeSymbol(symbol);
    return activeTrades.some((trade) => normalizeSymbol(trade.symbol) === target);
  }, [activeTrades, normalizeSymbol]);

  const confirmActivePositionAction = useCallback((symbol: string, action: string): boolean => {
    if (!symbolHasPosition(symbol)) return true;
    return confirm(`У ${symbol} есть активная позиция. Вы точно хотите ${action}?`);
  }, [symbolHasPosition]);

  useEffect(() => {
    setNewProfile((current) => getFallbackProfile(newStrategy, current));
  }, [newStrategy, getFallbackProfile]);

  const fetchRuntimeStatus = useCallback(async () => {
    try {
      const status = await getRuntimeStatus();
      setRuntimeStatus(status);
      setRuntimeError(null);
    } catch (err) {
      setRuntimeError(err instanceof Error ? err.message : 'Runtime status unavailable');
    }
  }, []);

  useEffect(() => {
    fetchRuntimeStatus();
    const timer = window.setInterval(fetchRuntimeStatus, 5000);
    return () => window.clearInterval(timer);
  }, [fetchRuntimeStatus]);

  const requestRuntimeAction = async (action: RuntimeAction, handler: () => Promise<unknown>) => {
    if (action === 'stop' && !confirm('Остановить торговый runtime?')) return;
    if (action === 'restart' && !confirm('Перезапустить торговый runtime?')) return;

    setRuntimeAction(action);
    try {
      await handler();
      onSuccess(`Команда ${runtimeActionLabel(action).toLowerCase()} отправлена`);
      await fetchRuntimeStatus();
      window.setTimeout(fetchRuntimeStatus, 1200);
    } catch (err) {
      const text = err instanceof Error ? err.message : 'Runtime command failed';
      onError(text);
      setRuntimeError(text);
    } finally {
      setRuntimeAction(null);
    }
  };

  const handleProfileChange = async (symbol: string, profile: string, instanceId?: string) => {
    if (!confirmActivePositionAction(symbol, 'изменить профиль и перезапустить процесс этого символа')) return;
    setSavingSymbol(instanceId || symbol);
    try {
      if (instanceId) {
        reportChange(await applyRuntimeConfigChange({
          save: () => setSymbolProfile(symbol, profile, instanceId),
          refresh: onRefresh,
          queue: () => restartSymbolRuntime(instanceId, symbol, 'profile_changed'),
        }));
      }
    } catch (err) {
      onError(getRuntimeErrorMessage(err, 'Failed to update profile'));
    } finally {
      setSavingSymbol(null);
    }
  };

  const handleAddInstance = async () => {
    if (!newSymbol.trim()) return;
    const selectedStrategy = newStrategy || strategies?.available[0] || 'HYBRID';
    const selectedProfile = getFallbackProfile(selectedStrategy, newProfile);
    setAdding(true);
    try {
      reportChange(await applyRuntimeConfigChange({
        save: () => createStrategyInstance({
          symbol: newSymbol.trim().toUpperCase(), strategy: selectedStrategy,
          profile: selectedProfile, enabled: true,
        }),
        refresh: onRefresh,
        queue: result => startSymbolRuntime(result.instance.id, result.instance.symbol, 'instance_created'),
      }));
      setNewSymbol('');
    } catch (err) {
      onError(getRuntimeErrorMessage(err, 'Failed to add strategy instance'));
    } finally {
      setAdding(false);
    }
  };

  const handleStrategyChange = async (instance: StrategyInstance, strategy: string) => {
    const nextProfile = getFallbackProfile(strategy, instance.profile || 'default');
    const data: Partial<StrategyInstance> = { strategy };
    if (nextProfile !== instance.profile) {
      data.profile = nextProfile;
    }
    await handleUpdateInstance(instance, data);
  };

  const handleUpdateInstance = async (instance: StrategyInstance, data: Partial<StrategyInstance>) => {
    const disabling = data.enabled === false && instance.enabled;
    const enabling = data.enabled === true && !instance.enabled;
    const configChanged = data.strategy !== undefined || data.profile !== undefined;

    if ((disabling || configChanged) && !confirmActivePositionAction(
      instance.symbol,
      disabling
        ? 'выключить торговлю и остановить процесс этого символа'
        : 'изменить конфигурацию и перезапустить процесс этого символа'
    )) {
      return;
    }

    setSavingSymbol(instance.id);
    try {
      const queue = disabling ? () => stopSymbolRuntime(instance.id, instance.symbol, 'instance_disabled')
        : enabling ? () => startSymbolRuntime(instance.id, instance.symbol, 'instance_enabled')
        : configChanged ? () => restartSymbolRuntime(instance.id, instance.symbol, 'instance_config_changed')
        : undefined;
      reportChange(await applyRuntimeConfigChange({
        save: () => updateStrategyInstance(instance.id, data), refresh: onRefresh, queue,
      }));
    } catch (err) {
      onError(getRuntimeErrorMessage(err, 'Failed to update strategy instance'));
    } finally {
      setSavingSymbol(null);
    }
  };

  const handleDeleteInstance = async (instance: StrategyInstance) => {
    if (!confirm(`Remove ${instance.id}?`)) return;
    if (!confirmActivePositionAction(instance.symbol, 'удалить instance и остановить процесс этого символа')) return;
    setSavingSymbol(instance.id);
    try {
      const status = await getRuntimeStatus();
      if (canControlWorker(status)) {
        await stopSymbolRuntime(instance.id, instance.symbol, 'instance_delete');
      } else if (status.stale || status.runtime_pid || !['stopped', 'crashed'].includes(status.state)) {
        throw new Error('Для удаления instance нужен актуальный статус работающего или остановленного runtime.');
      }
      await deleteStrategyInstance(instance.id);
      await onRefresh();
      onSuccess(`${instance.id}: конфигурация удалена`);
    } catch (err) {
      onError(getRuntimeErrorMessage(err, 'Failed to remove strategy instance'));
    } finally {
      setSavingSymbol(null);
    }
  };

  const handleRestartInstance = async (instance: StrategyInstance) => {
    if (!workerControlEnabled) return;
    if (!confirmActivePositionAction(instance.symbol, 'перезапустить процесс этого символа')) return;
    setSavingSymbol(instance.id);
    try {
      await restartSymbolRuntime(instance.id, instance.symbol, 'manual_instance_restart');
      onSuccess(`${instance.id} restart queued`);
      window.setTimeout(onRefresh, 1200);
    } catch (err) {
      onError(getRuntimeErrorMessage(err, 'Failed to restart strategy instance'));
    } finally {
      setSavingSymbol(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Section title="Bot Runtime">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <StatusDot active={runtimeState === 'running'} size="lg" />
            <div className="flex flex-col min-w-0">
              <span className={`text-sm font-medium ${
                runtimeState === 'crashed' ? 'text-red-400' :
                runtimeState === 'unavailable' || runtimeStatus?.stale ? 'text-amber-400' :
                'text-tg-text'
              }`}>
                {runtimeStateLabel(runtimeState)}
              </span>
              <span className="text-[10px] text-tg-hint">
                Supervisor PID: {runtimeStatus?.supervisor_pid ?? '-'}
              </span>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={fetchRuntimeStatus}
            disabled={runtimeAction !== null}
          >
            Обновить
          </Button>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-xl bg-tg-bg p-3 border border-white/10">
            <div className="text-[10px] uppercase text-tg-hint">Runtime PID</div>
            <div className="text-sm font-semibold text-tg-text">{runtimeStatus?.runtime_pid ?? '-'}</div>
          </div>
          <div className="rounded-xl bg-tg-bg p-3 border border-white/10">
            <div className="text-[10px] uppercase text-tg-hint">Started</div>
            <div className="text-sm font-semibold text-tg-text">{formatRuntimeTime(runtimeStatus?.started_at)}</div>
          </div>
          <div className="rounded-xl bg-tg-bg p-3 border border-white/10">
            <div className="text-[10px] uppercase text-tg-hint">Command</div>
            <div className="text-sm font-semibold text-tg-text capitalize">{runtimeStatus?.last_command_action || '-'}</div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <Button
            variant="secondary"
            onClick={() => requestRuntimeAction('start', startRuntime)}
            disabled={!runtimeControlEnabled || runtimeBusy || runtimeRunning}
            isLoading={runtimeAction === 'start'}
          >
            Старт
          </Button>
          <Button
            variant="danger"
            onClick={() => requestRuntimeAction('stop', stopRuntime)}
            disabled={!runtimeControlEnabled || runtimeBusy || runtimeStopped}
            isLoading={runtimeAction === 'stop'}
          >
            Стоп
          </Button>
          <Button
            variant="outline"
            onClick={() => requestRuntimeAction('restart', restartRuntime)}
            disabled={!runtimeControlEnabled || runtimeBusy || !runtimeRunning}
            isLoading={runtimeAction === 'restart'}
          >
            Рестарт
          </Button>
        </div>

        {(runtimeError || runtimeStatus?.last_error || runtimeStatus?.stale) && (
          <div className="text-xs text-amber-400 px-3 py-2 rounded-lg bg-amber-500/10">
            {runtimeError || runtimeStatus?.last_error || 'Supervisor status устарел'}
          </div>
        )}
      </Section>

      {!workerControlEnabled && <p role="status" className="text-sm text-tg-hint leading-relaxed">
        Включение instance сохраняет конфигурацию, но не запускает общий runtime. Нажмите «Старт» в блоке выше.
        Если runtime завершился с ошибкой, сначала устраните указанную причину. Графики появятся после начала сбора данных;
        в read_only сделки не открываются.
      </p>}

      <Section title="Runtime Overview">
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-xl bg-tg-bg p-3 border border-white/10">
            <div className="text-[10px] uppercase text-tg-hint">Instances</div>
            <div className="text-lg font-semibold text-tg-text">{instances.length}</div>
          </div>
          <div className="rounded-xl bg-tg-bg p-3 border border-white/10">
            <div className="text-[10px] uppercase text-tg-hint">Symbols</div>
            <div className="text-lg font-semibold text-tg-text">{symbolProfiles.symbols.length}</div>
          </div>
          <div className="rounded-xl bg-tg-bg p-3 border border-white/10">
            <div className="text-[10px] uppercase text-tg-hint">Disabled</div>
            <div className="text-lg font-semibold text-tg-text">{symbolProfiles.disabled_symbols.length}</div>
          </div>
        </div>
      </Section>

      <Section title="Add Instance" badge="restart">
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
            onChange={(e) => {
              const strategy = e.target.value;
              setNewStrategy(strategy);
              setNewProfile((current) => getFallbackProfile(strategy, current));
            }}
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
            {newCompatibleProfiles.map((profile) => (
              <option key={profile} value={profile}>{profile}</option>
            ))}
          </select>
          <Button
            variant="secondary"
            onClick={handleAddInstance}
            disabled={adding || savingSymbol !== null || !newSymbol.trim()}
          >
            Add
          </Button>
        </div>
      </Section>

      {instances.length > 0 && (
        <Section title="Instances" badge="restart">
          <div className="flex flex-col gap-3">
            {instances.map((instance) => {
              const isSaving = savingSymbol === instance.id;
              const isDisabled = !instance.enabled || symbolProfiles.disabled_symbols.some((symbol) => normalizeSymbol(symbol) === normalizeSymbol(instance.symbol));
              const compatibleProfiles = getCompatibleProfiles(instance.strategy);
              const selectedProfile = compatibleProfiles.includes(instance.profile || 'default')
                ? (instance.profile || 'default')
                : getFallbackProfile(instance.strategy, instance.profile || 'default');

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
                      {!isDisabled && !workerControlEnabled && <span className="text-xs text-tg-hint">Ожидает запуска runtime</span>}
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
                        {instance.enabled ? 'Включён' : 'Выключен'}
                      </button>
                      <button
                        onClick={() => handleRestartInstance(instance)}
                        disabled={isSaving || isDisabled || !workerControlEnabled}
                        className="text-xs px-2 py-1 rounded-lg border border-sky-500/30 text-sky-400 bg-sky-500/10 disabled:opacity-50"
                        title={workerControlEnabled ? 'Перезапустить worker' : 'Сначала запустите общий runtime'}
                      >
                        restart
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
                      onChange={(e) => handleStrategyChange(instance, e.target.value)}
                      disabled={isSaving}
                      className="tg-control text-xs rounded-lg px-2 py-1.5"
                    >
                      {(strategies?.available || [instance.strategy]).map((strategy) => (
                        <option key={strategy} value={strategy}>{strategy}</option>
                      ))}
                    </select>

                    <select
                      value={selectedProfile}
                      onChange={(e) => handleProfileChange(instance.symbol, e.target.value, instance.id)}
                      disabled={isSaving}
                      className="tg-control text-xs rounded-lg px-2 py-1.5"
                    >
                      {compatibleProfiles.map((profile) => (
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

      {instances.length === 0 && (
        <div className="text-center text-sm text-tg-hint px-4 py-6 rounded-xl bg-tg-section-bg border border-white/10">
          No strategy instances configured. Add a symbol, choose a strategy and profile, then start the bot runtime.
        </div>
      )}

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
