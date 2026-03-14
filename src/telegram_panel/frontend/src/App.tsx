import { useState, useCallback, useEffect } from 'react';
import type { TabId } from './api/types';
import { useWebSocket } from './hooks/useWebSocket';
import { useTelegram } from './hooks/useTelegram';
import { TabBar } from './components/TabBar';
import { Spinner } from './components/Spinner';
import { Dashboard } from './pages/Dashboard';
import { Charts } from './pages/Charts';
import { Trades } from './pages/Trades';
import { Logs } from './pages/Logs';
import { Settings } from './pages/Settings';
import { Journal } from './pages/Journal';
import { getDashboard } from './api/client';

// Detect if we're using web token from URL
function isWebTokenMode(): boolean {
  const urlParams = new URLSearchParams(window.location.search);
  const token = urlParams.get('token');
  return !!token;
}

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>('dashboard');
  const [authError, setAuthError] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const { subscribe, isConnected } = useWebSocket();
  const isWebMode = isWebTokenMode();
  useTelegram();

  // Проверка авторизации при загрузке (с retry для Telegram SDK race condition)
  useEffect(() => {
    let cancelled = false;
    const tryAuth = (attempt: number) => {
      getDashboard()
        .then(() => { if (!cancelled) setAuthChecked(true); })
        .catch((err) => {
          if (cancelled) return;
          // Retry once after 800ms (SDK might not be ready)
          if (attempt < 2) {
            setTimeout(() => tryAuth(attempt + 1), 800);
            return;
          }
          setAuthError(err instanceof Error ? err.message : 'Ошибка авторизации');
          setAuthChecked(true);
        });
    };
    tryAuth(0);
    return () => { cancelled = true; };
  }, []);

  const handleTabChange = useCallback((tab: TabId) => {
    setActiveTab(tab);
  }, []);

  // Загрузка
  if (!authChecked) {
    return (
      <div className="h-full flex items-center justify-center">
        <Spinner size={32} />
      </div>
    );
  }

  // Ошибка авторизации
  if (authError) {
    return (
      <div className="h-full flex items-center justify-center p-8">
        <div className="flex flex-col items-center gap-4 text-center max-w-xs">
          <div className="text-5xl">🔒</div>
          <p className="text-base font-semibold text-red-400">{authError}</p>
          <p className="text-sm text-tg-text/70">
            {isWebMode
              ? "Ссылка истекла. Напишите /weblink боту для новой ссылки."
              : "Используйте кнопку «Open Panel» в Telegram-боте"
            }
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-2 px-4 py-2 bg-tg-button text-tg-button-text text-sm rounded-lg"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const renderPage = () => {
    switch (activeTab) {
      case 'dashboard':
        return <Dashboard subscribe={subscribe} />;
      case 'charts':
        return <Charts subscribe={subscribe} />;
      case 'trades':
        return <Trades subscribe={subscribe} />;
      case 'logs':
        return <Logs subscribe={subscribe} />;
      case 'settings':
        return <Settings />;
      case 'journal':
        return <Journal subscribe={subscribe} />;
    }
  };

  return (
    <div className="h-full flex flex-col no-overscroll">
      {/* Connection indicator */}
      {!isConnected && (
        <div className="bg-amber-500/20 text-amber-400 text-xs text-center py-1 px-2">
          Reconnecting...
        </div>
      )}

      {/* Page content */}
      <div className="flex-1 overflow-y-auto pb-16">
        {renderPage()}
      </div>

      <TabBar activeTab={activeTab} onTabChange={handleTabChange} />
    </div>
  );
}
