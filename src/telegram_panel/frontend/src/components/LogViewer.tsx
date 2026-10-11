import { useLayoutEffect, useRef } from 'react';
import type { LogEvent, LogLevel } from '../api/types';

interface LogViewerProps {
  events: LogEvent[];
  follow?: boolean;
  onFollowChange?: (follow: boolean) => void;
}

const COLORS: Record<LogLevel, string> = {
  DEBUG: 'text-tg-hint', INFO: 'text-tg-text', WARNING: 'text-amber-400',
  ERROR: 'text-red-400', CRITICAL: 'text-red-400',
};
const dateFormat = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'medium', timeZone: 'UTC' });

export function LogViewer({ events, follow = true, onFollowChange }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const previousRef = useRef({ first: '', height: 0 });
  const lastRef = useRef('');

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const previous = previousRef.current;
    const first = events[0]?.event_id || '';
    const last = events[events.length - 1]?.event_id || '';
    const prepended = previous.first && previous.first !== first && events.some((event) => event.event_id === previous.first);
    if (prepended) container.scrollTop += container.scrollHeight - previous.height;
    else if (follow && (last !== lastRef.current || previous.first === first)) container.scrollTop = container.scrollHeight;
    previousRef.current = { first, height: container.scrollHeight };
    lastRef.current = last;
  }, [events, follow]);

  if (!events.length) return <p className="flex items-center justify-center h-40 text-tg-hint text-sm">По выбранным фильтрам событий нет.</p>;

  return <div
    ref={containerRef}
    role="region"
    aria-label="События журнала, время UTC"
    tabIndex={0}
    onScroll={() => {
      const container = containerRef.current;
      if (container) {
        previousRef.current.height = container.scrollHeight;
        if (follow && container.scrollHeight - container.scrollTop - container.clientHeight > 48) onFollowChange?.(false);
      }
    }}
    className="overflow-y-auto font-mono text-xs leading-5 bg-black/30 rounded-lg p-3 max-h-[60vh] focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button"
  >
    {events.map((event) => <article
      key={event.event_id}
      className="py-2 border-b border-white/5 min-w-0"
      style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 100px', overflowWrap: 'anywhere' }}
    >
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-tg-hint">
        <time dateTime={event.timestamp} title={event.timestamp}>{dateFormat.format(new Date(event.timestamp))} UTC</time>
        <span className={COLORS[event.level] || 'text-tg-text'}>{event.level}</span>
        <span>{event.category}</span>
        <span>{event.exchange || event.service}{event.symbol ? ` · ${event.symbol}` : ''}</span>
        {event.instance_id && <span>instance: {event.instance_id}</span>}
        {event.order_id && <span>order: {event.order_id}</span>}
        {event.request_id && <span>request: {event.request_id}</span>}
        {event.duration_ms !== null && <span>{event.duration_ms} мс</span>}
      </div>
      <p className={`whitespace-pre-wrap break-words ${COLORS[event.level] || 'text-tg-text'}`}>{event.message}</p>
      {event.exception && <details className="mt-1 text-tg-hint">
        <summary className="cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button">Подробности ошибки</summary>
        <pre className="whitespace-pre-wrap break-words text-red-400">{event.exception}</pre>
      </details>}
      <span className="sr-only">ID события {event.event_id}</span>
    </article>)}
  </div>;
}
