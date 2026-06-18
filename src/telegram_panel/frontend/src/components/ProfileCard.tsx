import { useState } from 'react';
import type { ProfileInfo } from '../api/client';

interface ProfileCardProps {
  profile: ProfileInfo;
  name: string;
  onEdit: () => void;
  onDelete: () => void;
  onClone: () => void;
  isUsed?: boolean;
  usageCount?: number;
}

export function ProfileCard({
  profile,
  name,
  onEdit,
  onDelete,
  onClone,
  isUsed = false,
  usageCount = 0,
}: ProfileCardProps) {
  const [showActions, setShowActions] = useState(false);
  const isDefault = name === 'default';
  const isAutoCreated = name.startsWith('auto-');

  // Type assertion for profile properties
  const profileObj = profile as Record<string, unknown>;
  const profileStrategy = profileObj._strategy as string | undefined;
  const profileDescription = (profileObj._description as string) || (profileObj.description as string) || 'No description';

  return (
    <div className="bg-tg-section-bg rounded-xl p-3 mb-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 flex-1">
          <span className="font-medium text-tg-text">{name}</span>

          {/* Badges */}
          {isDefault && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
              DEFAULT
            </span>
          )}
          {isAutoCreated && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400">
              AUTO
            </span>
          )}
          {profileStrategy && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-hint">
              {profileStrategy}
            </span>
          )}
        </div>

        {!isDefault && (
          <button
            onClick={() => setShowActions(!showActions)}
            className="p-1 text-tg-hint hover:text-tg-text"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" />
            </svg>
          </button>
        )}
      </div>

      {/* Description */}
      <div className="text-[10px] text-tg-hint mt-1 text-left">
        {profileDescription}
      </div>

      {/* Usage info */}
      {isUsed && (
        <div className="text-[10px] text-tg-hint mt-1">
          Used by {usageCount} instance{usageCount !== 1 ? 's' : ''}
        </div>
      )}

      {/* Settings preview - show all sections */}
      <div className="mt-2 pt-2 border-t border-white/10">
        {/* Dynamically render all profile sections except underscore-prefixed */}
        {Object.entries(profileObj)
          .filter(([key]) => !key.startsWith('_'))
          .map(([sectionName, sectionData]) => {
            if (typeof sectionData !== 'object' || sectionData === null || Array.isArray(sectionData)) {
              return null;
            }
            const entries = Object.entries(sectionData as Record<string, unknown>);
            if (entries.length === 0) return null;

            // Format section name
            const sectionLabels: Record<string, string> = {
              preset: 'Preset',
              position: 'Position',
              signal_rules: 'Signal Rules',
              sl_tp: 'SL/TP',
              breakeven: 'Breakeven',
              time_exit: 'Time Exit',
              risk_limits: 'Risk Limits',
              loops: 'Loops',
              regime_overrides: 'Regime',
              interaction_rules: 'Interaction',
              ai_integration: 'AI',
              ai_filter: 'AI Filter',
              sessions: 'Sessions',
              multi_timeframe: 'MTF',
              pre_filter: 'Pre Filter',
              grid_settings: 'Grid',
            };
            const label = sectionLabels[sectionName] || sectionName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

            return (
              <div key={sectionName} className="mb-2">
                <div className="text-[9px] text-tg-hint uppercase mb-1">{label}</div>
                <div className="flex flex-wrap gap-1">
                  {entries.slice(0, 4).map(([k, v]) => (
                    <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-text">
                      {k}: {String(v)}
                    </span>
                  ))}
                  {entries.length > 4 && (
                    <span className="text-[10px] text-tg-hint">
                      +{entries.length - 4} more
                    </span>
                  )}
                </div>
              </div>
            );
          })}
      </div>

      {/* Actions dropdown */}
      {showActions && !isDefault && (
        <div className="mt-3 pt-2 border-t border-white/10 flex gap-2">
          <button
            onClick={() => {
              setShowActions(false);
              onEdit();
            }}
            className="flex-1 py-1.5 px-3 rounded-lg bg-tg-button text-tg-button-text text-xs font-medium hover:bg-blue-500"
          >
            Edit
          </button>
          <button
            onClick={() => {
              setShowActions(false);
              onClone();
            }}
            className="flex-1 py-1.5 px-3 rounded-lg bg-tg-section-bg text-tg-text text-xs font-medium hover:bg-white/10"
          >
            Clone
          </button>
          <button
            onClick={() => {
              setShowActions(false);
              onDelete();
            }}
            disabled={isUsed}
            title={isUsed ? 'Cannot delete: profile is in use' : 'Delete'}
            className={`py-1.5 px-3 rounded-lg text-xs font-medium ${
              isUsed
                ? 'bg-gray-600/20 text-gray-500 cursor-not-allowed'
                : 'bg-red-600/20 text-red-400 hover:bg-red-600/30'
            }`}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
