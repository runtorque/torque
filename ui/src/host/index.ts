import { browserHost } from './browser';
import { createTauriHost } from './tauri';
import type { DesktopHost } from './types';

declare global {
  interface Window {
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  }
}

export function isTauriRuntime(target: Window = window): boolean {
  return Boolean(target.__TAURI_INTERNALS__ || target.__TAURI__);
}

export function createDesktopHost(target: Window = window): DesktopHost {
  return isTauriRuntime(target) ? createTauriHost() : browserHost;
}

export * from './browser';
export * from './tauri';
export * from './types';
