import type { McpEntry } from '../../types';
import { jsonContentForValidation } from '../../utils/jsonExample';

const BASE_FIELDS = new Set(['name', 'transport', 'url', 'headers', 'auth_headers', 'timeout_s', 'enabled']);

export function customConfigFromMcpEntry(entry: McpEntry): string {
  const custom = Object.fromEntries(Object.entries(entry).filter(([key]) => !BASE_FIELDS.has(key)));
  return Object.keys(custom).length ? JSON.stringify(custom, null, 2) : '';
}

export function parseMcpCustomConfig(raw: string): Record<string, unknown> | undefined {
  const content = jsonContentForValidation(raw);
  if (!content) return {};
  try {
    const value: unknown = JSON.parse(content);
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return undefined;
    return value as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

export function reservedMcpCustomFields(custom: Record<string, unknown>): string[] {
  return Object.keys(custom).filter((key) => BASE_FIELDS.has(key));
}
