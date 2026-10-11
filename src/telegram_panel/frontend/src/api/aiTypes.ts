export type AIProvider = 'openrouter' | 'deepseek' | 'lmstudio';

export interface AIProfile {
  base_url: string;
  model: string;
}

export interface DeepSeekThinking {
  enabled: boolean;
  effort: 'low' | 'high' | 'max';
}

export interface AIOverride {
  provider: AIProvider | null;
  model: string | null;
  temperature: number;
  max_tokens: number;
}

export interface AISettings {
  provider: AIProvider;
  providers: {
    openrouter: AIProfile;
    deepseek: AIProfile & { thinking?: DeepSeekThinking };
    lmstudio: AIProfile;
  };
  temperature: number;
  max_tokens: number;
  request_timeout: number;
  retry_count: number;
  retry_backoff_base: number;
  reasoning: { enabled: boolean; effort: 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'; exclude: boolean };
  provider_routing: { allow_fallbacks: boolean };
  fallback_models: string[];
  overrides: { veto: AIOverride; regime: AIOverride };
}

export interface ProviderStatus {
  id: AIProvider;
  label: string;
  requires_api_key: boolean;
  has_api_key: boolean;
  configured: boolean;
  hint?: string;
}

export interface AISettingsResponse {
  settings: AISettings;
  providers: ProviderStatus[];
}

export interface AISaveResponse {
  settings: AISettings;
  needs_restart: boolean;
}

export interface AIProbeResponse {
  provider: AIProvider;
  ok: boolean;
  latency_ms: number;
  message: string;
  model?: string;
  error_code?: string;
}

export interface AIModelsResponse {
  provider: AIProvider;
  models: { id: string }[];
}
