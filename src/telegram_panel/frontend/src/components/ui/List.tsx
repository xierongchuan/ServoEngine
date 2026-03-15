import React from 'react';
import { Switch } from './Switch';

interface ListRowProps {
  label: string;
  value?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

export function ListRow({ label, value, children, className = '' }: ListRowProps) {
  return (
    <div className={`flex items-center justify-between py-2 border-b border-white/5 last:border-0 ${className}`}>
      <span className="text-sm font-medium text-tg-text">{label}</span>
      {value !== undefined ? (
        <span className="text-sm text-tg-hint">{value}</span>
      ) : children}
    </div>
  );
}

interface ListInputRowProps extends Omit<ListRowProps, 'children'> {
  type?: 'text' | 'number';
  value: string | number | undefined;
  onChange: (value: any) => void;
  editing: boolean;
  step?: number | string;
  placeholder?: string;
  displayValue?: React.ReactNode;
}

export function ListInputRow({ 
  label, 
  value, 
  onChange, 
  editing, 
  type = 'number', 
  step, 
  placeholder,
  displayValue,
  className 
}: ListInputRowProps) {
  return (
    <ListRow label={label} className={className}>
      {editing ? (
        <input
          type={type}
          value={value ?? ''}
          onChange={(e) => {
            const val = type === 'number' ? parseFloat(e.target.value) : e.target.value;
            onChange(isNaN(val as number) && type === 'number' ? 0 : val);
          }}
          step={step}
          className="w-24 bg-tg-bg text-tg-text text-right text-sm rounded-lg px-2 py-1 border border-white/10 outline-none focus:border-tg-button focus:ring-1 focus:ring-tg-button"
          placeholder={placeholder}
        />
      ) : (
        <span className="text-sm text-tg-hint font-medium">
          {displayValue !== undefined ? displayValue : (value ?? 'N/A')}
        </span>
      )}
    </ListRow>
  );
}

interface ListToggleRowProps extends Omit<ListRowProps, 'children' | 'value'> {
  value: boolean;
  onChange: (value: boolean) => void;
  editing: boolean;
}

export function ListToggleRow({ label, value, onChange, editing, className }: ListToggleRowProps) {
  return (
    <ListRow label={label} className={className}>
      <Switch
        checked={value}
        onCheckedChange={onChange}
        disabled={!editing}
      />
    </ListRow>
  );
}
