import type { AIProvider, AISettings, AIOverride, DeepSeekThinking } from '../../api/aiTypes';

export const AI_PROVIDERS: { id: AIProvider; label: string; env: string; hint: string }[] = [
  { id: 'openrouter', label: 'OpenRouter', env: 'OPENROUTER_API_KEY', hint: 'Каталог моделей и маршрутизация OpenRouter.' },
  { id: 'deepseek', label: 'DeepSeek', env: 'DEEPSEEK_API_KEY', hint: 'Официальный API DeepSeek, без посредника.' },
  { id: 'lmstudio', label: 'LM Studio', env: 'LM_STUDIO_API_KEY', hint: 'Локальный OpenAI-совместимый сервер. Из Podman используйте host.containers.internal, не localhost.' },
];

export function defaultDeepSeekThinking(): DeepSeekThinking {
  return { enabled: false, effort: 'high' };
}

export function defaultAISettings(): AISettings {
  return {
    provider: 'openrouter',
    providers: {
      openrouter: { base_url: 'https://openrouter.ai/api/v1', model: 'google/gemini-3.1-flash-lite-preview' },
      deepseek: { base_url: 'https://api.deepseek.com', model: 'deepseek-flash', thinking: defaultDeepSeekThinking() },
      lmstudio: { base_url: 'http://host.containers.internal:1234/v1', model: '' },
    },
    temperature: 0.3, max_tokens: 4096, request_timeout: 60, retry_count: 3, retry_backoff_base: 2,
    reasoning: { enabled: true, effort: 'high', exclude: true },
    provider_routing: { allow_fallbacks: true }, fallback_models: [],
    overrides: {
      veto: { provider: null, model: null, temperature: 0.1, max_tokens: 100 },
      regime: { provider: null, model: null, temperature: 0.2, max_tokens: 150 },
    },
  };
}

export type AIDraft = Omit<AISettings, 'fallback_models'> & { fallback_models: string };
export type AIFieldErrors = Record<string, string>;

export function toAIDraft(settings: AISettings): AIDraft {
  return { ...settings, providers: {
    openrouter: { ...settings.providers.openrouter },
        deepseek: { ...settings.providers.deepseek, ...(settings.providers.deepseek.thinking ? { thinking: { ...settings.providers.deepseek.thinking } } : {}) },
        lmstudio: { ...settings.providers.lmstudio },
  }, reasoning: { ...settings.reasoning }, provider_routing: { ...settings.provider_routing },
  overrides: { veto: { ...settings.overrides.veto }, regime: { ...settings.overrides.regime } },
  fallback_models: settings.fallback_models.join('\n') };
}

export function fromAIDraft(draft: AIDraft): AISettings {
  return { ...draft, providers: Object.fromEntries(AI_PROVIDERS.map(({ id }) => [id, {
    base_url: draft.providers[id].base_url.trim(), model: draft.providers[id].model.trim(),
    ...(id === 'deepseek' && draft.providers.deepseek.thinking ? { thinking: { ...draft.providers.deepseek.thinking } } : {}),
  }])) as AISettings['providers'],
  overrides: Object.fromEntries(['veto', 'regime'].map((key) => {
    const override = draft.overrides[key as keyof AISettings['overrides']];
    return [key, { ...override, model: override.model?.trim() || null }];
  })) as AISettings['overrides'],
  fallback_models: [...new Set(draft.fallback_models.split('\n').map((model) => model.trim()).filter(Boolean))] };
}

export function sameAIProfile(saved: AISettings, draft: AIDraft, provider: AIProvider, includeThinking = false): boolean {
  return saved.providers[provider].base_url === draft.providers[provider].base_url.trim()
    && saved.providers[provider].model === draft.providers[provider].model.trim()
    && (!includeThinking || provider !== 'deepseek'
      || JSON.stringify(saved.providers.deepseek.thinking) === JSON.stringify(draft.providers.deepseek.thinking));
}

export function effectiveAIOverride(settings: AISettings, override: AIOverride) {
  const provider = override.provider ?? settings.provider;
  return { provider, model: override.model ?? settings.providers[provider].model };
}

export function validateAIDraft(draft: AIDraft): AIFieldErrors {
  const errors: AIFieldErrors = {};
  const settings = fromAIDraft(draft);
  const providerIds = AI_PROVIDERS.map(({ id }) => id);
  if (!providerIds.includes(settings.provider)) errors.provider = 'Выберите провайдера.';
  const modelError = (model: string) => !/^[A-Za-z0-9][A-Za-z0-9_./:@+\-]{0,255}$/.test(model) || /^sk-[A-Za-z0-9_-]{8,}/.test(model);
  for (const { id } of AI_PROVIDERS) {
    const profile = settings.providers[id];
    try {
      const url = new URL(profile.base_url);
      if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash
        || (id !== 'lmstudio' && url.protocol !== 'https:') || url.port === '0'
        || profile.base_url.length > 2048 || /\s/.test(profile.base_url)) throw new Error();
    } catch {
      errors[`providers.${id}.base_url`] = `Укажите ${id === 'lmstudio' ? 'HTTP(S)' : 'HTTPS'} URL без пароля, пробелов, query-параметров и фрагмента.`;
    }
    if ((profile.model && modelError(profile.model)) || (!profile.model && (id !== 'lmstudio' || settings.provider === id))) {
      errors[`providers.${id}.model`] = 'Укажите ID модели (латиница, цифры, _./:@+−; до 256 символов), не API-ключ.';
    }
  }
  const thinking = settings.providers.deepseek.thinking;
  if (thinking) {
    if (typeof thinking.enabled !== 'boolean') errors['providers.deepseek.thinking.enabled'] = 'Укажите, включён ли thinking.';
    if (!['low', 'high', 'max'].includes(thinking.effort)) errors['providers.deepseek.thinking.effort'] = 'Выберите low, high или max.';
  }
  const number = (key: string, value: number, min: number, max: number, integer = false) => {
    if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
      errors[key] = `Укажите ${integer ? 'целое число' : 'число'} от ${min} до ${max}.`;
    }
  };
  number('temperature', settings.temperature, 0, 2);
  number('max_tokens', settings.max_tokens, 1, 1000000, true);
  number('request_timeout', settings.request_timeout, 1, 300);
  number('retry_count', settings.retry_count, 0, 5, true);
  number('retry_backoff_base', settings.retry_backoff_base, 1, 10);
  if (!['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].includes(settings.reasoning.effort)) errors['reasoning.effort'] = 'Выберите уровень рассуждений.';
  if (settings.fallback_models.length > 20 || settings.fallback_models.some(modelError)) errors.fallback_models = 'До 20 моделей. Каждый ID — с новой строки, до 256 символов, без секретов.';
  for (const key of ['veto', 'regime'] as const) {
    const override = settings.overrides[key];
    if (override.provider !== null && !providerIds.includes(override.provider)) {
      errors[`overrides.${key}.provider`] = 'Выберите провайдера или наследование.';
    } else {
      const effective = effectiveAIOverride(settings, override);
      if (!effective.model || modelError(effective.model)) errors[`overrides.${key}.model`] = 'Укажите ID модели или заполните модель наследуемого профиля.';
    }
    number(`overrides.${key}.temperature`, override.temperature, 0, 2);
    number(`overrides.${key}.max_tokens`, override.max_tokens, 1, 1000000, true);
  }
  return errors;
}
