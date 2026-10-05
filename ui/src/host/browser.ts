import {
  HostCapabilityError,
  type DesktopCapability,
  type DesktopHost,
  type DetachedWindow,
  type WindowBounds,
} from './types';

const browserCapabilities: ReadonlySet<DesktopCapability> = new Set(['open-external']);

function unavailable(capability: DesktopCapability): Promise<never> {
  return Promise.reject(new HostCapabilityError(capability));
}

export const browserHost: DesktopHost = {
  kind: 'browser',
  capabilities: browserCapabilities,
  detachPanel(): Promise<DetachedWindow> {
    return unavailable('detach-panel');
  },
  focusWindow(): Promise<void> {
    return unavailable('focus-window');
  },
  reattachWindow(): Promise<void> {
    return unavailable('reattach-window');
  },
  listDetachedWindows(): Promise<DetachedWindow[]> {
    return Promise.resolve([]);
  },
  currentWindowBounds(): Promise<WindowBounds | null> {
    return Promise.resolve(null);
  },
  revealLogDirectory(): Promise<void> {
    return unavailable('reveal-log-directory');
  },
  openExternal(url: string): Promise<void> {
    const parsed = new URL(url, window.location.href);
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      return Promise.reject(new Error(`Unsupported external URL scheme: ${parsed.protocol}`));
    }
    window.open(parsed.toString(), '_blank', 'noopener,noreferrer');
    return Promise.resolve();
  },
  confirm(): Promise<boolean> {
    return unavailable('native-confirm');
  },
  runtimeConfig(): Promise<null> {
    return Promise.resolve(null);
  },
};
