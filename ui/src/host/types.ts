export type HostKind = 'browser' | 'tauri' | 'electron';

export type DesktopCapability =
  | 'detach-panel'
  | 'focus-window'
  | 'reattach-window'
  | 'list-detached-windows'
  | 'window-bounds'
  | 'reveal-log-directory'
  | 'open-external'
  | 'native-confirm'
  | 'runtime-config';

export interface WindowBounds {
  /** Native captures are physical pixels; initial UI sizes remain logical. */
  physical?: boolean;
  display_id?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
}

export interface DetachPanelRequest {
  panel: string;
  bounds?: WindowBounds | null;
}

export interface DetachedWindow {
  label: string;
  panel: string;
  bounds?: WindowBounds | null;
}

export interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

export type DesktopRuntimeConfig = Record<string, unknown>;

export interface DesktopHost {
  readonly kind: HostKind;
  readonly capabilities: ReadonlySet<DesktopCapability>;
  detachPanel(request: DetachPanelRequest): Promise<DetachedWindow>;
  focusWindow(label: string): Promise<void>;
  reattachWindow(label: string): Promise<void>;
  listDetachedWindows(): Promise<DetachedWindow[]>;
  currentWindowBounds(): Promise<WindowBounds | null>;
  revealLogDirectory(): Promise<void>;
  openExternal(url: string): Promise<void>;
  confirm(request: ConfirmRequest): Promise<boolean>;
  runtimeConfig(): Promise<DesktopRuntimeConfig | null>;
}

export class HostCapabilityError extends Error {
  constructor(capability: DesktopCapability) {
    super(`Desktop capability is unavailable in this host: ${capability}`);
    this.name = 'HostCapabilityError';
  }
}

export function hasHostCapability(
  host: DesktopHost,
  capability: DesktopCapability,
): boolean {
  return host.capabilities.has(capability);
}
