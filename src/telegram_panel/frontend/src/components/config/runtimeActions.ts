import { getRuntimeStatus, type RuntimeStatus } from '../../api/client';

export function canControlWorker(status: RuntimeStatus | null): boolean {
  return !!status && status.state === 'running' && status.control_enabled && !status.stale
    && Number.isInteger(status.runtime_pid) && (status.runtime_pid ?? 0) > 0
    && !!status.execution && typeof status.execution === 'object' && !Array.isArray(status.execution);
}

export type RuntimeChangeNotice = { type: 'success' | 'warning'; text: string };

export async function applyRuntimeConfigChange<T>({ save, refresh, queue, readStatus = getRuntimeStatus }: {
  save: () => Promise<T>;
  refresh: () => void | Promise<void>;
  queue?: (saved: T) => Promise<unknown>;
  readStatus?: () => Promise<RuntimeStatus>;
}): Promise<RuntimeChangeNotice> {
  const saved = await save();
  // Конфиг уже сохранён: ошибка применения не должна оставлять старый UI или предлагать повтор PUT.
  try {
    await refresh();
  } catch {
    return { type: 'warning', text: 'Настройки сохранены, но обновление панели не удалось. Обновите страницу; команда worker не отправлена.' };
  }
  if (!queue) return { type: 'success', text: 'Настройки сохранены.' };
  let status: RuntimeStatus;
  try {
    status = await readStatus();
  } catch {
    return { type: 'warning', text: 'Настройки сохранены. Статус runtime недоступен; команда worker не отправлена. Проверьте статус перед применением.' };
  }
  if (!canControlWorker(status)) {
    if (!status.stale && !status.runtime_pid && ['stopped', 'crashed'].includes(status.state)) {
      return { type: 'success', text: 'Настройки сохранены. Runtime не запущен; изменения применятся при общем «Старт».' };
    }
    return { type: 'warning', text: 'Настройки сохранены. Runtime не готов к командам worker; дождитесь актуального статуса и примените изменения отдельно.' };
  }
  try {
    await queue(saved);
    return { type: 'success', text: 'Настройки сохранены; команда применения поставлена в очередь runtime.' };
  } catch {
    return { type: 'warning', text: 'Настройки сохранены, но команда worker не принята. Проверьте статус runtime; сохранение повторять не нужно.' };
  }
}
