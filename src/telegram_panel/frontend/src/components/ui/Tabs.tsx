import { ReactNode } from 'react';

export interface TabOption {
  value: string;
  label: ReactNode;
}

interface TabsProps {
  options: TabOption[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
}

export function Tabs({ options, value, onChange, className = '' }: TabsProps) {
  return (
    <div className={`flex gap-1 bg-tg-section-bg p-1 rounded-xl overflow-x-auto no-scrollbar ring-1 ring-white/5 ${className}`}>
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`flex flex-1 items-center justify-center gap-2 text-xs py-2 px-3 rounded-lg transition-all capitalize whitespace-nowrap font-medium ${
            value === opt.value 
              ? 'bg-tg-button text-zinc-900 shadow-sm' 
              : 'text-tg-hint hover:text-tg-text hover:bg-white/5'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
