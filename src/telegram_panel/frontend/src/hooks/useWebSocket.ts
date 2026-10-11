import { useEffect, useRef, useState, useCallback } from 'react';
import type { WSEvent } from '@/api/types';
import { AuthenticationError, getWebSocketUrl } from '../api/client';

type Callback = (data: Record<string, unknown>) => void;

export function useWebSocket() {
  const [lastEvent, setLastEvent] = useState<WSEvent | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const subscribersRef = useRef<Map<string, Set<Callback>>>(new Map());

  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let requestTimeout: ReturnType<typeof setTimeout> | undefined;
    let handshakeTimeout: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      if (!stopped) timer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 30000));
    };
    const connect = async () => {
      if (stopped) return;
      controller = new AbortController();
      requestTimeout = setTimeout(() => controller?.abort(), 10000);
      try {
        // Билет одноразовый: каждый reconnect получает новый через HTTP с auth-заголовками.
        const url = await getWebSocketUrl(controller.signal);
        if (stopped) return;
        socket = new WebSocket(url);
        handshakeTimeout = setTimeout(() => socket?.close(), 10000);
        socket.onopen = () => {
          clearTimeout(handshakeTimeout);
          setIsConnected(true);
          retry = 0;
        };
        socket.onmessage = (event) => {
          if (stopped) return;
          try {
            const parsed: WSEvent = JSON.parse(event.data);
            if (typeof parsed.type !== 'string') return;
            setLastEvent(parsed);
            subscribersRef.current.get(parsed.type)?.forEach(cb => cb(parsed.data || {}));
          } catch {
            // Некорректное событие не прерывает соединение.
          }
        };
        socket.onclose = (event) => {
          clearTimeout(handshakeTimeout);
          if (stopped) return;
          setIsConnected(false);
          // HTTP polling покажет предложение открыть свежую сессию; бесконечный auth retry не нужен.
          if (event.code !== 4401 && event.code !== 1008) schedule();
        };
        socket.onerror = () => socket?.close();
      } catch (error) {
        if (!stopped && !(error instanceof AuthenticationError)) schedule();
      } finally {
        clearTimeout(requestTimeout);
      }
    };
    void connect();
    return () => {
      stopped = true;
      controller?.abort();
      clearTimeout(timer);
      clearTimeout(requestTimeout);
      clearTimeout(handshakeTimeout);
      socket?.close();
    };
  }, []);

  const subscribe = useCallback((type: string, callback: Callback) => {
    if (!subscribersRef.current.has(type)) subscribersRef.current.set(type, new Set());
    subscribersRef.current.get(type)!.add(callback);
    return () => { subscribersRef.current.get(type)?.delete(callback); };
  }, []);

  return { lastEvent, isConnected, subscribe };
}
