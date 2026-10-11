import { APIError, AuthenticationError, fetchAPI } from './client';
import type { AIProvider, AISettings, AISettingsResponse, AISaveResponse, AIProbeResponse, AIModelsResponse } from './aiTypes';

export function getAISettings(signal?: AbortSignal) {
  return fetchAPI<AISettingsResponse>('/api/config/ai', { signal });
}

export function saveAISettings(settings: AISettings, signal?: AbortSignal) {
  return fetchAPI<AISaveResponse>('/api/config/ai', {
    method: 'PUT', body: JSON.stringify(settings), signal,
  });
}

export function probeAIProvider(provider: AIProvider, signal?: AbortSignal) {
  // Только GET-запрос к провайдеру; платная генерация никогда не включается из панели.
  return fetchAPI<AIProbeResponse>('/api/config/ai/probe', {
    method: 'POST', body: JSON.stringify({ provider, allow_paid_completion: false }), signal,
  });
}

export function getAIModels(provider: AIProvider, signal?: AbortSignal) {
  return fetchAPI<AIModelsResponse>(`/api/config/ai/models?provider=${encodeURIComponent(provider)}`, { signal });
}

export function aiErrorMessage(error: unknown): string {
  if (error instanceof AuthenticationError) return error.message;
  if (error instanceof APIError) {
    switch (error.status) {
      case 404: return 'API настроек ИИ недоступен. Проверьте версию backend.';
      case 409: return 'Настройки изменились на сервере. Загрузите их заново перед сохранением.';
      case 422: return 'Сервер отклонил настройки. Проверьте поля и допустимые параметры модели.';
      case 429: return 'Лимит запросов исчерпан. Повторите позже.';
      case 502: case 503: case 504: return 'Провайдер или сервер недоступен. Повторите позже.';
    }
  }
  // Не показываем детали исключений: они могут содержать заголовки авторизации.
  return 'Не удалось выполнить запрос. Проверьте соединение и повторите.';
}
