import { AuthenticationError, getLogEvents as fetchLogEvents, getLogSources as fetchLogSources } from './client';
import type { LogEventsPage, LogFilters, LogSources } from './types';
import type { LogFilters as APILogFilters } from './logTypes';

export class LogAPIError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

// Авторизация, URL и fetch принадлежат общему client; журнал не читает credentials.
async function request<T>(load: () => Promise<unknown>): Promise<T> {
  try {
    return await load() as T;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    const reportedStatus = typeof error === 'object' && error !== null && 'status' in error ? error.status : undefined;
    // Старый client не различает 401/403 в AuthenticationError; оба останавливают live.
    const status = typeof reportedStatus === 'number' ? reportedStatus
      : error instanceof AuthenticationError ? 401 : error instanceof SyntaxError ? 502 : 0;
    const message = status === 401 ? 'Сессия истекла. Откройте Mini App заново или получите новую /weblink.'
      : status === 403 ? 'Нет доступа к журналу.'
      : status === 409 ? 'Журнал или фильтры изменились. Нажмите «Обновить».'
      : status === 422 ? 'Проверьте фильтры и временной диапазон.'
      : status === 502 ? 'Некорректный ответ журнала. Повторите запрос.'
      : 'Не удалось загрузить журнал. Повторите запрос или сузьте фильтры.';
    throw new LogAPIError(status, message);
  }
}

export function getLogEvents(filters: LogFilters, cursor?: string | null, signal?: AbortSignal) {
  const query: APILogFilters = { limit: 100 };
  for (const key of ['level', 'category', 'exchange', 'symbol', 'instance', 'search', 'order', 'request', 'source', 'time_from', 'time_to'] as const) {
    const value = filters[key];
    if (value) query[key] = value;
  }
  if (cursor) query.cursor = cursor;
  return request<LogEventsPage>(() => fetchLogEvents(query, signal));
}

export function getLogSources(signal?: AbortSignal) {
  return request<LogSources>(() => fetchLogSources(signal));
}
