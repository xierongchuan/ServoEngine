export interface StatusDotProps {
  active: boolean;
  className?: string;
  size?: 'sm' | 'md' | 'lg';
}

export function StatusDot({ active, className = '', size = 'md' }: StatusDotProps) {
  const sizes = {
    sm: 'w-1.5 h-1.5',
    md: 'w-2 h-2',
    lg: 'w-2.5 h-2.5',
  };

  return (
    <span
      className={`rounded-full shrink-0 ${sizes[size]} ${
        active 
          ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' 
          : 'bg-gray-500'
      } ${className}`}
    />
  );
}
