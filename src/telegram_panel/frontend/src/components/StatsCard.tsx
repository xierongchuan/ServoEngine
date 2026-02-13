interface StatsCardProps {
  label: string;
  value: string | number;
  trend?: 'up' | 'down' | 'neutral';
}

export function StatsCard({ label, value, trend }: StatsCardProps) {
  return (
    <div className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-1">
      <span className="text-xs text-tg-hint">{label}</span>
      <div className="flex items-center gap-1.5">
        <span className="text-lg font-semibold text-tg-text">{value}</span>
        {trend && trend !== 'neutral' && (
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d={trend === 'up' ? 'M6 2L10 8H2L6 2Z' : 'M6 10L2 4H10L6 10Z'}
              fill={trend === 'up' ? '#22c55e' : '#ef4444'}
            />
          </svg>
        )}
      </div>
    </div>
  );
}
