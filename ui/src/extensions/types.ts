import type { ComponentType } from 'react';

export type TorquePanelModule = { default: ComponentType };

export interface TorquePanelExtension {
  id: string;
  title: string;
  defaultPlacement: 'bottom' | 'right' | 'floating';
  load: () => Promise<TorquePanelModule>;
  requiredRuntimeCapabilities?: readonly string[];
}

export interface TorqueExtensionRegistry {
  version: 1;
  panels: readonly TorquePanelExtension[];
}

export const CORE_PANEL_IDS: ReadonlySet<string> = new Set([
  'board',
  'agents',
  'engineer',
  'terminal',
  'planning',
  'control',
]);

export function validateExtensionRegistry(
  registry: TorqueExtensionRegistry,
  corePanelIds: ReadonlySet<string> = new Set(),
): TorqueExtensionRegistry {
  if (registry.version !== 1) throw new Error('Unsupported extension registry version');
  const seen = new Set<string>();
  for (const panel of registry.panels) {
    if (!/^[a-z][a-z0-9-]*$/.test(panel.id)) {
      throw new Error(`Invalid extension panel id: ${panel.id}`);
    }
    if (seen.has(panel.id) || corePanelIds.has(panel.id)) {
      throw new Error(`Duplicate extension panel id: ${panel.id}`);
    }
    if (typeof panel.title !== 'string' || !panel.title.trim() || panel.title.length > 80) {
      throw new Error(`Invalid extension panel title: ${panel.id}`);
    }
    if (!['bottom', 'right', 'floating'].includes(panel.defaultPlacement)) {
      throw new Error(`Invalid extension panel placement: ${panel.id}`);
    }
    if (typeof panel.load !== 'function') {
      throw new Error(`Extension panel loader is required: ${panel.id}`);
    }
    const capabilities = panel.requiredRuntimeCapabilities ?? [];
    const capabilitySet = new Set<string>();
    for (const capability of capabilities) {
      if (!/^[a-z][a-z0-9.-]*$/.test(capability) || capabilitySet.has(capability)) {
        throw new Error(`Invalid extension runtime capability: ${panel.id}`);
      }
      capabilitySet.add(capability);
    }
    seen.add(panel.id);
  }
  return registry;
}

export function composeExtensionRegistries(
  registries: readonly TorqueExtensionRegistry[],
  corePanelIds: ReadonlySet<string> = CORE_PANEL_IDS,
): TorqueExtensionRegistry {
  const panels: TorquePanelExtension[] = [];
  const reserved = new Set(corePanelIds);
  for (const registry of registries) {
    const validated = validateExtensionRegistry(registry, reserved);
    for (const panel of validated.panels) {
      reserved.add(panel.id);
      panels.push(panel);
    }
  }
  return { version: 1, panels };
}
