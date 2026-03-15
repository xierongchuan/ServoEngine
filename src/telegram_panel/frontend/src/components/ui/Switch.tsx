import React from 'react';

interface SwitchProps extends React.InputHTMLAttributes<HTMLInputElement> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
}

export function Switch({ checked, onCheckedChange, disabled = false, className = '', ...props }: SwitchProps) {
  return (
    <label className={`relative inline-flex items-center cursor-pointer ${disabled ? 'opacity-50 cursor-not-allowed' : ''} ${className}`}>
      <input
        type="checkbox"
        className="sr-only peer"
        checked={checked}
        onChange={(e) => {
          if (!disabled) onCheckedChange(e.target.checked);
        }}
        disabled={disabled}
        {...props}
      />
      <div className="w-11 h-6 bg-tg-bg peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-tg-button ring-1 ring-white/10 peer-checked:ring-tg-button"></div>
    </label>
  );
}
