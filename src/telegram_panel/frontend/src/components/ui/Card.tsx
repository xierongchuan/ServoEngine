import React from 'react';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  badge?: 'hot-reload' | 'restart' | string;
  children: React.ReactNode;
}

export function Card({ title, badge, children, className = '', ...props }: CardProps) {
  return (
    <div className={`flex flex-col gap-3 ${className}`} {...props}>
      {(title || badge) && (
        <div className="flex items-center justify-between px-1">
          {title && <span className="text-sm font-medium text-tg-section-header uppercase tracking-wider">{title}</span>}
          {badge === 'hot-reload' && (
            <span className="text-[9px] uppercase px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-medium tracking-wide">
              Hot Reload
            </span>
          )}
          {badge === 'restart' && (
            <span className="text-[9px] uppercase px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 font-medium tracking-wide">
              Requires Restart
            </span>
          )}
          {badge && badge !== 'hot-reload' && badge !== 'restart' && (
            <span className="text-[9px] uppercase px-1.5 py-0.5 rounded bg-tg-section-bg text-tg-hint font-medium tracking-wide">
              {badge}
            </span>
          )}
        </div>
      )}
      <div className="bg-tg-section-bg rounded-2xl p-4 shadow-sm ring-1 ring-white/5 flex flex-col gap-3">
        {children}
      </div>
    </div>
  );
}
