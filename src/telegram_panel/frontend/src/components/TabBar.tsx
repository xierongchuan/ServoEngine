import type { TabId } from '../api/types';

interface Tab {
  id: TabId;
  label: string;
  icon: JSX.Element;
}

const tabs: Tab[] = [
  {
    id: 'dashboard',
    label: 'Home',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <rect x="2" y="2" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <rect x="11" y="2" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <rect x="2" y="11" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <rect x="11" y="11" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
  {
    id: 'charts',
    label: 'Charts',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <path d="M3 17V7l4 4 4-8 6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    id: 'trades',
    label: 'Trades',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <path d="M5 10L10 5L15 10M5 15L10 10L15 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    id: 'logs',
    label: 'Logs',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <path d="M4 5h12M4 8h10M4 11h12M4 14h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <circle cx="10" cy="10" r="3" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M4.2 15.8l1.4-1.4M14.4 5.6l1.4-1.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    id: 'journal',
    label: 'AI',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <circle cx="10" cy="8" r="4" stroke="currentColor" strokeWidth="1.5" />
        <path d="M6 14c0-2.2 1.8-4 4-4s4 1.8 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M10 4V2M7 5L5.5 3.5M13 5l1.5-1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
];

interface TabBarProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

export function TabBar({ activeTab, onTabChange }: TabBarProps) {
  const handleClick = (tab: TabId) => {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.impactOccurred('light');
    } catch {}
    onTabChange(tab);
  };

  return (
    <nav className="fixed z-50 bg-tg-bg transition-all
      bottom-0 left-0 right-0 border-t border-white/5 pb-safe
      md:top-0 md:bottom-0 md:w-20 md:right-auto md:border-t-0 md:border-r md:pb-0
    ">
      <div className="flex items-center justify-between h-14 w-full px-2 gap-1
        md:flex-col md:justify-center md:h-full md:gap-8 md:px-0 md:w-auto
      ">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => handleClick(tab.id)}
              className={`flex flex-1 flex-col items-center justify-center gap-1 py-1.5 transition-all rounded-xl ${
                isActive ? 'text-tg-button bg-tg-button/10' : 'text-tg-hint hover:text-tg-text hover:bg-white/5'
              } md:flex-none md:w-14 md:h-14`}
            >
              {tab.icon}
              <span className="text-[10px] md:text-[9px] font-medium">{tab.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
