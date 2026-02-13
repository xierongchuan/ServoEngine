import { useEffect, useMemo } from 'react';

export function useTelegram() {
  const webApp = useMemo(() => window.Telegram?.WebApp, []);

  useEffect(() => {
    if (webApp) {
      webApp.ready();
      webApp.expand();
    }
  }, [webApp]);

  return {
    webApp: webApp ?? null,
    user: webApp?.initDataUnsafe?.user ?? null,
    colorScheme: webApp?.colorScheme ?? 'dark',
    themeParams: webApp?.themeParams ?? {},
    initData: webApp?.initData ?? '',
  };
}
