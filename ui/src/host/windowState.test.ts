import { describe, expect, it } from 'vitest';
import { releaseWindow, savedWindowBounds } from './windowState';
describe('native ownership and geometry', () => {
  const bounds = { x: 924, y: 420, width: 1952, height: 1308, physical: true, display_id: 'Retina' };
  it('retains saved pixels on release and ignores a close from an older window', () => {
    const windows = { agents: { label: 'new', bounds } };
    expect(releaseWindow(windows, 'agents', 'old')).toBe(windows);
    expect(releaseWindow(windows, 'agents', 'new')).toEqual({ agents: { label: '', bounds } });
    expect(savedWindowBounds(releaseWindow(windows, 'agents', 'new').agents, { width: 900, height: 640 })).toEqual(bounds);
    expect(savedWindowBounds({ bounds: { width: -1 } }, { width: 900, height: 640 })).toEqual({ width: 900, height: 640 });
  });
});
