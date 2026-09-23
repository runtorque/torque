import { describe, expect, it } from 'vitest';
import { fixedNavigatorScope, reviewShortcutChange } from './shortcutBindings';
import { eventMatchesBinding, type KeybindingDescriptor } from './preferences';
const binding = (key: string, flags: Partial<KeybindingDescriptor> = {}): KeybindingDescriptor => ({ key, ctrl: false, meta: false, alt: false, shift: false, ...flags });
describe('shortcut conflict contracts', () => {
  it('handles the palette platform alias and preserves additional modifiers at dispatch', () => {
    expect(reviewShortcutChange({}, 'task.create', binding('k', { ctrl: true })).conflicts).toEqual(['navigator.open']);
    const modified = binding('k', { meta: true, alt: true, shift: true });
    expect(eventMatchesBinding(new KeyboardEvent('keydown', { key: 'K', ctrlKey: true, altKey: true, shiftKey: true }), modified, true)).toBe(true);
    expect(eventMatchesBinding(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }), modified, true)).toBe(false);
    expect(reviewShortcutChange({ 'navigator.open': modified }, 'task.create', binding('k', { ctrl: true })).conflicts).toEqual([]);
    expect(reviewShortcutChange({ 'navigator.open': modified }, 'task.create', binding('K', { ctrl: true, alt: true, shift: true })).conflicts).toEqual(['navigator.open']);
  });
  it('does not confuse distinct modifiers and removes only redundant overrides after a swap', () => {
    expect(reviewShortcutChange({}, 'task.create', binding('b', { alt: true })).conflicts).toEqual([]);
    const first = reviewShortcutChange({ legacy: { key: 'j' } }, 'task.create', binding('b'));
    expect(first.canReassign).toBe(true); expect(first.next).toMatchObject({ legacy: { key: 'j' }, 'task.create': { key: 'b' }, 'react.panel.board': { key: 'n' } });
    expect(reviewShortcutChange(first.next, 'task.create', null).next).toEqual({ legacy: { key: 'j' } });
  });
  it('blocks fixed navigation and refuses swaps that would preserve existing duplicate bindings', () => {
    for (const key of ['Enter', 'ArrowDown', 'Tab', 'Space', '/']) expect(reviewShortcutChange({}, 'task.create', binding(key)).fixed).toBeTruthy();
    for (const flags of [{ meta: true }, { ctrl: true }]) expect(reviewShortcutChange({}, 'task.create', binding('p', flags)).fixed).toBe('Open panel navigator');
    const duplicate = { 'react.panel.agents': binding('b') };
    expect(reviewShortcutChange(duplicate, 'task.create', binding('b')).canReassign).toBe(false);
    const previousDuplicate = { 'react.panel.agents': binding('n') };
    expect(reviewShortcutChange(previousDuplicate, 'task.create', binding('b')).canReassign).toBe(false);
  });
  it('keeps group and panel entry points restricted to the unmodified platform chords', () => {
    expect(fixedNavigatorScope(new KeyboardEvent('keydown', { key: 'G', metaKey: true }))).toBe('groups');
    expect(fixedNavigatorScope(new KeyboardEvent('keydown', { key: 'p', ctrlKey: true }))).toBe('panels');
    expect(fixedNavigatorScope(new KeyboardEvent('keydown', { key: 'p', ctrlKey: true, shiftKey: true }))).toBeNull();
    expect(fixedNavigatorScope(new KeyboardEvent('keydown', { key: 'g' }))).toBeNull();
  });
});
