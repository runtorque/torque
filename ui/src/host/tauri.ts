import { invoke as tauriInvoke } from '@tauri-apps/api/core';

import type {
  ConfirmRequest,
  DesktopCapability,
  DesktopHost,
  DesktopRuntimeConfig,
  DetachPanelRequest,
  DetachedWindow,
  WindowBounds,
} from './types';

export type TauriInvoker = (
  command: string,
  args?: Record<string, unknown>,
) => Promise<unknown>;

const tauriCapabilities: ReadonlySet<DesktopCapability> = new Set([
  'detach-panel',
  'focus-window',
  'reattach-window',
  'list-detached-windows',
  'window-bounds',
  'reveal-log-directory',
  'open-external',
  'native-confirm',
  'runtime-config',
]);

export function createTauriHost(
  invoke: TauriInvoker = (command, args) => tauriInvoke(command, args),
): DesktopHost {
  const call = <T,>(command: string, args?: Record<string, unknown>) =>
    invoke(command, args) as Promise<T>;
  return {
    kind: 'tauri',
    capabilities: tauriCapabilities,
    async detachPanel(request: DetachPanelRequest): Promise<DetachedWindow> {
      const label = await call<string>('detach', {
        panel: request.panel,
        bounds: request.bounds ?? null,
      });
      return {
        label,
        panel: request.panel,
        ...(request.bounds !== undefined ? { bounds: request.bounds } : {}),
      };
    },
    focusWindow(label: string): Promise<void> {
      return call('focus_window', { label });
    },
    reattachWindow(label: string): Promise<void> {
      return call('reattach', { label });
    },
    listDetachedWindows(): Promise<DetachedWindow[]> {
      return call('list_detached');
    },
    currentWindowBounds(): Promise<WindowBounds | null> {
      return call('current_window_bounds');
    },
    revealLogDirectory(): Promise<void> {
      return call('reveal_log_dir');
    },
    openExternal(url: string): Promise<void> {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) {
        return Promise.reject(new Error(`Unsupported external URL scheme: ${parsed.protocol}`));
      }
      return call('open_external', { url: parsed.toString() });
    },
    confirm(request: ConfirmRequest): Promise<boolean> {
      return call('confirm', {
        message: request.message,
        title: request.title,
        confirmLabel: request.confirmLabel ?? null,
        cancelLabel: request.cancelLabel ?? null,
        destructive: request.destructive ?? false,
      });
    },
    runtimeConfig(): Promise<DesktopRuntimeConfig | null> {
      return call('runtime_config');
    },
  };
}
