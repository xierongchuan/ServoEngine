/**
 * Утилиты для работы с профилями
 * - Форматирование имён параметров
 * - Определение типов данных
 * - Валидация параметров против схемы
 */

/**
 * Форматирует техническое имя параметра в человекочитаемый формат
 * Примеры:
 * - position_check_interval -> Position Check Interval
 * - atr_sl_mult -> Atr Sl Mult
 * - minScoreForSignal -> Min Score For Signal
 * - min_score -> Min Score
 */
export function formatParameterName(key: string): string {
  // Заменяем underscores и camelCase на пробелы
  const withSpaces = key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');

  // Первую букву каждого слова делаем заглавной
  return withSpaces
    .split(' ')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

/**
 * Определяет тип данных значения для выбора правильного инпута
 */
export type ValueType = 'number' | 'string' | 'boolean' | 'array' | 'object';

export function getValueType(value: unknown): ValueType {
  if (value === null || value === undefined) return 'string';
  if (typeof value === 'number') return 'number';
  if (typeof value === 'boolean') return 'boolean';
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'object') return 'object';
  return 'string';
}

/**
 * Преобразует значение в строку для отображения в инпуте
 */
export function valueToInputString(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (Array.isArray(value)) return JSON.stringify(value);
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/**
 * Парсит значение из строки инпута обратно в правильный тип
 */
export function parseInputValue(inputStr: string, expectedType: ValueType): unknown {
  const trimmed = inputStr.trim();

  if (trimmed === '') return null;

  switch (expectedType) {
    case 'number':
      const num = Number(trimmed);
      return isNaN(num) ? trimmed : num;
    case 'boolean':
      return trimmed.toLowerCase() === 'true' || trimmed === '1';
    case 'array':
    case 'object':
      try {
        return JSON.parse(trimmed);
      } catch {
        return trimmed;
      }
    default:
      return trimmed;
  }
}

/**
 * Проверяет, является ли ключ допустимым согласно схеме
 */
export function isKeyValid(
  sectionName: string,
  keyName: string,
  schema: Record<string, string[]>
): boolean {
  // Получаем допустимые ключи для секции
  const validKeys = schema[sectionName] || [];

  // Если схема для секции пустая (неограниченная) - разрешаем
  if (!validKeys || validKeys.length === 0) return true;

  // Проверяем ключ
  return validKeys.includes(keyName);
}

/**
 * Получает все допустимые секции из схемы
 */
export function getValidSections(schema: Record<string, string[]>): string[] {
  return Object.keys(schema);
}

/**
 * Фильтрует профиль, оставляя только допустимые ключи согласно схеме
 * Также добавляет значения по умолчанию из схемы для отсутствующих ключей
 */
export function filterProfileBySchema(
  profile: Record<string, unknown>,
  schema: Record<string, string[]>,
  defaultSchema: Record<string, string[]>
): Record<string, unknown> {
  const result: Record<string, unknown> = {};

  // Копируем метаданные
  for (const key of Object.keys(profile)) {
    if (key.startsWith('_')) {
      result[key] = profile[key];
    }
  }

  // Получаем стратегию профиля
  const strategy = (profile._strategy as string)?.toUpperCase() || '';
  const strategySchema = schema[strategy] || defaultSchema;

  // Обрабатываем каждую секцию
  for (const sectionName of Object.keys(profile)) {
    if (sectionName.startsWith('_')) continue;

    const sectionData = profile[sectionName];
    if (typeof sectionData !== 'object' || sectionData === null || Array.isArray(sectionData)) {
      result[sectionName] = sectionData;
      continue;
    }

    // Получаем допустимые ключи для этой секции
    const validKeys: string[] = (strategySchema[sectionName as keyof typeof strategySchema] || defaultSchema[sectionName as keyof typeof defaultSchema] || []) as string[];

    // Фильтруем ключи секции
    const filteredSection: Record<string, unknown> = {};
    for (const key of Object.keys(sectionData)) {
      // Разрешаем если ключ есть в схеме или схема неограниченная
      if (validKeys.length === 0 || validKeys.includes(key)) {
        filteredSection[key] = (sectionData as Record<string, unknown>)[key];
      }
    }

    result[sectionName] = filteredSection;
  }

  return result;
}
