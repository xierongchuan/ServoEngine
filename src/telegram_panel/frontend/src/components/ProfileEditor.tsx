import { useState, useEffect } from 'react';
import { getProfileSchema, type ProfileSchema } from '../api/client';
import {
  formatParameterName,
  getValueType,
  valueToInputString,
  parseInputValue,
} from '../utils/profile';

interface ProfileEditorProps {
  profile: Record<string, unknown>;
  profileName: string;
  onSave: (profile: Record<string, unknown>) => void;
  onCancel: () => void;
  isLoading?: boolean;
  error?: string | null;
}

export function ProfileEditor({
  profile,
  profileName,
  onSave,
  onCancel,
  isLoading = false,
  error,
}: ProfileEditorProps) {
  const [schema, setSchema] = useState<ProfileSchema | null>(null);
  const [editedProfile, setEditedProfile] = useState<Record<string, unknown>>(profile);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  // Загружаем схему при монтировании
  useEffect(() => {
    getProfileSchema()
      .then(setSchema)
      .catch(err => {
        console.error('Failed to load profile schema:', err);
        setSchemaError('Failed to load parameter schema');
      });
  }, []);

  // Обновляем editedProfile когда меняется profile
  useEffect(() => {
    setEditedProfile(profile);
  }, [profile]);

  // Обработчик изменения значения параметра
  const handleValueChange = (
    sectionName: string,
    keyName: string,
    newValue: string
  ) => {
    const section = editedProfile[sectionName];
    if (typeof section !== 'object' || section === null) return;

    const currentValue = (section as Record<string, unknown>)[keyName];
    const valueType = getValueType(currentValue);
    const parsedValue = parseInputValue(newValue, valueType);

    setEditedProfile({
      ...editedProfile,
      [sectionName]: {
        ...section,
        [keyName]: parsedValue,
      },
    });
  };

  // Форматирование названия секции
  const formatSectionName = (name: string): string => {
    // Специальные названия для известных секций
    const sectionNames: Record<string, string> = {
      preset: 'Preset Settings',
      position: 'Position Settings',
      signal_rules: 'Signal Rules',
      sl_tp: 'Stop Loss / Take Profit',
      breakeven: 'Breakeven',
      time_exit: 'Time Exit',
      risk_limits: 'Risk Limits',
      loops: 'Loops',
      regime_overrides: 'Regime Overrides',
      interaction_rules: 'Interaction Rules',
      ai_integration: 'AI Integration',
      ai_filter: 'AI Filter',
      sessions: 'Sessions',
      multi_timeframe: 'Multi Timeframe',
      pre_filter: 'Pre Filter',
      grid_settings: 'Grid Settings',
    };

    return sectionNames[name] || formatParameterName(name);
  };

  // Получаем допустимые ключи для секции из схемы
  const getValidKeysForSection = (sectionName: string): string[] => {
    if (!schema) return [];

    const strategy = (profile._strategy as string)?.toUpperCase() || '';
    const strategySchema = schema.schemas[strategy];
    const defaultSchema = schema.default;

    // Сначала проверяем стратегию
    if (strategySchema && strategySchema[sectionName]) {
      return strategySchema[sectionName];
    }

    // Потом default
    if (defaultSchema[sectionName]) {
      return defaultSchema[sectionName];
    }

    // Если схема не найдена, возвращаем ключи из профиля
    const section = profile[sectionName];
    if (typeof section === 'object' && section !== null) {
      return Object.keys(section);
    }

    return [];
  };

  // Рендерим одну секцию параметров
  const renderSection = (sectionName: string) => {
    const section = editedProfile[sectionName];
    if (typeof section !== 'object' || section === null || Array.isArray(section)) {
      return null;
    }

    const validKeys = getValidKeysForSection(sectionName);
    const entries = Object.entries(section);

    if (entries.length === 0) return null;

    return (
      <div key={sectionName} className="mb-4">
        <div className="text-xs text-tg-hint mb-2 uppercase tracking-wide">
          {formatSectionName(sectionName)}
        </div>
        <div className="space-y-2">
          {entries.map(([key, value]) => {
            const valueType = getValueType(value);
            const inputValue = valueToInputString(value);
            const isValidKey = validKeys.length === 0 || validKeys.includes(key);

            return (
              <div key={key} className="flex items-center gap-2">
                <label
                  className="text-xs text-tg-text w-32 truncate flex-shrink-0"
                  title={key}
                >
                  {formatParameterName(key)}
                </label>
                {valueType === 'boolean' ? (
                  <select
                    value={inputValue}
                    onChange={(e) => handleValueChange(sectionName, key, e.target.value)}
                    className="tg-control flex-1 px-2 py-1 rounded-lg text-xs"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : valueType === 'number' ? (
                  <input
                    type="number"
                    step="any"
                    value={inputValue}
                    onChange={(e) => handleValueChange(sectionName, key, e.target.value)}
                    className="tg-control flex-1 px-2 py-1 rounded-lg text-xs"
                  />
                ) : valueType === 'array' || valueType === 'object' ? (
                  <input
                    type="text"
                    value={inputValue}
                    onChange={(e) => handleValueChange(sectionName, key, e.target.value)}
                    className="tg-control flex-1 px-2 py-1 rounded-lg text-xs font-mono"
                  />
                ) : (
                  <input
                    type="text"
                    value={inputValue}
                    onChange={(e) => handleValueChange(sectionName, key, e.target.value)}
                    className="tg-control flex-1 px-2 py-1 rounded-lg text-xs"
                  />
                )}
                {!isValidKey && (
                  <span className="text-[10px] text-amber-400" title="Invalid parameter">
                    ⚠️
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // Получаем все секции из профиля (кроме метаданных)
  const getProfileSections = (): string[] => {
    return Object.keys(editedProfile).filter(
      (key) => !key.startsWith('_')
    );
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-tg-section-bg rounded-xl p-4 w-[320px] max-h-[80vh] overflow-y-auto shadow-xl">
        <h3 className="text-sm font-medium text-tg-text mb-3">
          Edit Profile: {profileName}
        </h3>

        {schemaError && (
          <div className="text-xs text-amber-400 mb-3 p-2 bg-amber-500/10 rounded">
            {schemaError}
          </div>
        )}

        {/* Description */}
        <div className="mb-4">
          <label className="text-xs text-tg-hint mb-1 block">Description</label>
          <input
            type="text"
            value={(editedProfile._description as string) || ''}
            onChange={(e) =>
              setEditedProfile({ ...editedProfile, _description: e.target.value })
            }
            className="tg-control px-3 py-2 rounded-lg text-sm"
          />
        </div>

        {/* Dynamic Sections */}
        {getProfileSections().map(renderSection)}

        {/* Error */}
        {error && (
          <p className="text-xs text-red-400 mb-3">{error}</p>
        )}

        {/* Actions */}
        <div className="flex gap-2 mt-4">
          <button
            onClick={onCancel}
            className="flex-1 py-2 px-3 rounded-lg bg-tg-bg text-tg-text text-sm font-medium hover:bg-white/10"
          >
            Cancel
          </button>
          <button
            onClick={() => onSave(editedProfile)}
            disabled={isLoading}
            className="flex-1 py-2 px-3 rounded-lg bg-tg-button text-tg-button-text text-sm font-medium hover:bg-blue-500 disabled:opacity-50"
          >
            {isLoading ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
