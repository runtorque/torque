import { describe, expect, it } from 'vitest';

import {
  descriptorFromKeyboardEvent,
  effectiveBinding,
  eventMatchesBinding,
  readAppearance,
} from './preferences';

describe('workspace preferences', () => {
  it('normalizes bounded client-local appearance values', () => {
    const storage = { getItem: () => JSON.stringify({ contrast: 'high', accent: 'teal', scale: 200, terminalFont: 2, density: 'comfortable', reduceMotion: true }) };
    expect(readAppearance(storage)).toEqual({ contrast: 'high', accent: 'teal', scale: 125, terminalFont: 10, density: 'comfortable', reduceMotion: true });
  });

  it('uses durable overrides before React defaults', () => {
    expect(effectiveBinding({ keybindings: { 'task.create': { key: 't', ctrl: true } } }, 'task.create')).toEqual({ key: 't', ctrl: true, meta: false, alt: false, shift: false });
    expect(effectiveBinding({}, 'react.panel.board')?.key).toBe('b');
  });

  it('captures and matches complete modifier descriptors', () => {
    const descriptor = descriptorFromKeyboardEvent({ key: 'T', ctrlKey: true, metaKey: false, altKey: false, shiftKey: true });
    expect(descriptor).toEqual({ key: 't', ctrl: true, meta: false, alt: false, shift: true });
    expect(eventMatchesBinding(new KeyboardEvent('keydown', { key: 't', ctrlKey: true, shiftKey: true }), descriptor)).toBe(true);
    expect(eventMatchesBinding(new KeyboardEvent('keydown', { key: 't', ctrlKey: true }), descriptor)).toBe(false);
  });
});
