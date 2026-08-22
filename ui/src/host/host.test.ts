import { describe, expect, it, vi } from 'vitest';

import { browserHost } from './browser';
import { createTauriHost } from './tauri';
import { HostCapabilityError } from './types';

describe('browser host', () => {
  it('fails closed for native-only operations', async () => {
    expect(browserHost.kind).toBe('browser');
    expect(browserHost.capabilities).toEqual(new Set(['open-external']));
    await expect(browserHost.detachPanel({ panel: 'board' })).rejects.toBeInstanceOf(
      HostCapabilityError,
    );
    await expect(browserHost.runtimeConfig()).resolves.toBeNull();
  });

  it('rejects unsafe external URL schemes', async () => {
    await expect(browserHost.openExternal('file:///etc/passwd')).rejects.toThrow(
      'Unsupported external URL scheme',
    );
  });
});

describe('Tauri host', () => {
  it('routes operations through the allow-listed command adapter', async () => {
    const invoke = vi.fn((command: string) => {
      return Promise.resolve(command === 'detach' ? 'panel-board-1' : null);
    });
    const host = createTauriHost(invoke);

    await expect(host.detachPanel({ panel: 'board' })).resolves.toEqual({
      label: 'panel-board-1',
      panel: 'board',
    });
    expect(invoke).toHaveBeenCalledWith('detach', { panel: 'board', bounds: null });
    expect(host.capabilities.has('detach-panel')).toBe(true);
  });

  it('passes native confirm arguments in the Rust command shape', async () => {
    const invoke = vi.fn(() => Promise.resolve(true));
    const host = createTauriHost(invoke);
    await expect(host.confirm({ title: 'Confirm', message: 'Proceed?' })).resolves.toBe(true);
    expect(invoke).toHaveBeenCalledWith('confirm', {
      message: 'Proceed?',
      title: 'Confirm',
      confirmLabel: null,
      cancelLabel: null,
      destructive: false,
    });
  });
});
