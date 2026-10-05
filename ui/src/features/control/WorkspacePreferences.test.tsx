import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ShortcutPreferencesPanel } from './WorkspacePreferences';

describe('shortcut preferences', () => {
  it('reviews a conflicting rebind before swapping both actions and can cancel without edits', () => {
    const changed = vi.fn();
    function Form() { const [keybindings, setBindings] = useState<Record<string, unknown>>({}); return <ShortcutPreferencesPanel settings={{ keybindings }} onChange={(next) => { changed(next); setBindings(next); }} />; }
    render(<Form />); const input = screen.getByRole('textbox', { name: 'Create task shortcut' });
    fireEvent.keyDown(input, { key: 'b' });
    expect(screen.getByRole('alert')).toHaveTextContent('Open Board'); expect(changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel reassignment' })); expect(input).toHaveValue('N');
    fireEvent.keyDown(input, { key: 'b' }); fireEvent.click(screen.getByRole('button', { name: 'Reassign shortcut' }));
    expect(input).toHaveValue('B'); expect(screen.getByRole('textbox', { name: 'Open Board shortcut' })).toHaveValue('N'); expect(changed).toHaveBeenCalledTimes(1);
    fireEvent.click(within(input.closest('article')!).getByRole('button', { name: 'Reset' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Open Board'); expect(changed).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Reassign shortcut' })); expect(input).toHaveValue('N'); expect(screen.getByRole('textbox', { name: 'Open Board shortcut' })).toHaveValue('B');
    expect(changed).toHaveBeenLastCalledWith({});
  });
  it('requires another review if assignments change before confirmation', () => {
    const changed = vi.fn(); const view = render(<ShortcutPreferencesPanel settings={{ keybindings: {} }} onChange={changed} />);
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Create task shortcut' }), { key: 'b' });
    view.rerender(<ShortcutPreferencesPanel settings={{ keybindings: { 'task.create': { key: 't' } } }} onChange={changed} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reassign shortcut' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Shortcuts changed'); expect(changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Reassign shortcut' }));
    expect(changed.mock.calls[0]?.[0] as Record<string, unknown>).toMatchObject({ 'react.panel.board': { key: 't' } });
  });
  it('ignores composing and repeated key events', () => {
    const changed = vi.fn(); render(<ShortcutPreferencesPanel settings={{}} onChange={changed} />);
    const input = screen.getByRole('textbox', { name: 'Create task shortcut' });
    fireEvent.keyDown(input, { key: 't', isComposing: true }); fireEvent.keyDown(input, { key: 't', repeat: true }); expect(changed).not.toHaveBeenCalled();
  });
  it('blocks fixed navigation collisions and leaves Tab available for normal focus traversal', () => {
    const changed = vi.fn(); render(<ShortcutPreferencesPanel settings={{ keybindings: {} }} onChange={changed} />);
    const input = screen.getByRole('textbox', { name: 'Create task shortcut' });
    fireEvent.keyDown(input, { key: 'g', metaKey: true });
    expect(screen.getByRole('alert')).toHaveTextContent('fixed'); expect(screen.queryByRole('button', { name: 'Reassign shortcut' })).not.toBeInTheDocument(); expect(changed).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(input, { key: 'Tab' })).toBe(true); expect(changed).not.toHaveBeenCalled();
  });
});
