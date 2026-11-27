import type { AdvancedConfigTemplate } from '../../types';

export const ADVANCED_CONFIG_SWITCH_FIELDS = [
  'progressive_tool_enabled',
  'progressive_tool_optimize',
  'a4p_enabled',
  'auto_memory_enabled',
  'enable_kv_cache_affinity',
] as const;

export type AdvancedConfigSwitchField = (typeof ADVANCED_CONFIG_SWITCH_FIELDS)[number];

export type AdvancedConfigSwitchState = Pick<
  AdvancedConfigTemplate,
  AdvancedConfigSwitchField
>;

export function defaultAdvancedConfigSwitches(): AdvancedConfigSwitchState {
  return {
    progressive_tool_enabled: true,
    progressive_tool_optimize: false,
    a4p_enabled: false,
    auto_memory_enabled: false,
    enable_kv_cache_affinity: false,
  };
}

export function switchesFromTemplate(
  template: AdvancedConfigTemplate | null | undefined,
): AdvancedConfigSwitchState {
  const defaults = defaultAdvancedConfigSwitches();
  if (!template) return defaults;
  return {
    progressive_tool_enabled: template.progressive_tool_enabled,
    progressive_tool_optimize: template.progressive_tool_optimize,
    a4p_enabled: template.a4p_enabled,
    auto_memory_enabled: template.auto_memory_enabled,
    enable_kv_cache_affinity: template.enable_kv_cache_affinity,
  };
}

export function enabledSwitchFields(state: AdvancedConfigSwitchState): AdvancedConfigSwitchField[] {
  return ADVANCED_CONFIG_SWITCH_FIELDS.filter((key) => state[key]);
}