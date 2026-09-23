import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsSearch } from './SettingsSearch';

describe('settings search', () => {
  it('distinguishes scopes, opens nested disclosures, and uses Enter without submitting', () => {
    const submit = vi.fn();
    function Form() { const form = useRef<HTMLFormElement>(null); return <form ref={form} onSubmit={submit}><SettingsSearch form={form} /><section><h3>Group</h3><details><summary>Engineer defaults</summary><details><summary>Notifications</summary><label>Enabled<input defaultValue="draft" /></label></details></details><details><summary>Architect defaults</summary><label>Enabled<input /></label></details></section></form>; }
    render(<Form />); const search = screen.getByRole('searchbox');
    fireEvent.change(search, { target: { value: 'enabled' } });
    expect(screen.getByRole('status')).toHaveTextContent('2 matching settings');
    fireEvent.change(search, { target: { value: 'engineer enabled' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    const input = screen.getByDisplayValue('draft');
    expect(input).toHaveFocus(); expect(input).toHaveValue('draft');
    expect(input.closest('details')?.parentElement?.closest('details')).toHaveAttribute('open');
    expect(input.closest('details')).toHaveAttribute('open'); expect(submit).not.toHaveBeenCalled(); expect(search).toHaveValue('');
  });
  it('searches descriptions but never indexes draft values or option text', () => {
    function Form() { const form = useRef<HTMLFormElement>(null); return <form ref={form}><SettingsSearch form={form} /><label>API key<input type="password" defaultValue="fixture-secret-value" /></label><label>Notes<textarea defaultValue="private-note-value" /></label><label>Mode<select defaultValue="full"><option value="full">Full-option-value</option></select></label><label>Days<input aria-describedby="days-help" /></label><small id="days-help">Zero disables age expiry</small></form>; }
    render(<Form />); const search = screen.getByRole('searchbox');
    for (const value of ['fixture-secret-value', 'private-note-value', 'full-option-value']) { fireEvent.change(search, { target: { value } }); expect(screen.getByRole('status')).toHaveTextContent('No settings found'); }
    fireEvent.change(search, { target: { value: 'age expiry' } }); expect(screen.getByRole('button', { name: /Days/ })).toBeVisible();
    fireEvent.keyDown(search, { key: 'Escape' }); expect(search).toHaveFocus(); expect(search).toHaveValue(''); expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
  it('refreshes matching mounted controls and removes stale results without resetting the query', async () => {
    function Form() { const form = useRef<HTMLFormElement>(null); const [newField, setNewField] = useState(false); return <form ref={form}><SettingsSearch form={form} /><button type="button" onClick={() => setNewField((value) => !value)}>Refresh fields</button>{newField ? <label>Updated timeout<input key="new" /></label> : <label>Original timeout<input key="old" /></label>}</form>; }
    render(<Form />); const search = screen.getByRole('searchbox');
    fireEvent.change(search, { target: { value: 'timeout' } }); expect(screen.getByRole('button', { name: /Original timeout/ })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh fields' }));
    await screen.findByRole('button', { name: /Updated timeout/ }); expect(screen.queryByRole('button', { name: /Original timeout/ })).not.toBeInTheDocument(); expect(search).toHaveValue('timeout');
    fireEvent.keyDown(search, { key: 'ArrowDown' }); expect(screen.getByRole('button', { name: /Updated timeout/ })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: /Updated timeout/ })); expect(screen.getByRole('textbox', { name: 'Updated timeout' })).toHaveFocus();
  });
  it('finds appearance toggle buttons and updates result availability during pending saves', async () => {
    function Form() { const form = useRef<HTMLFormElement>(null); const [saving, setSaving] = useState(false); return <form ref={form}><SettingsSearch form={form} /><button type="button" onClick={() => setSaving((value) => !value)}>Toggle saving</button><fieldset disabled={saving}><section><h3>Appearance</h3><button type="button" aria-label="Blue accent" aria-pressed="true" /></section></fieldset></form>; }
    render(<Form />); const search = screen.getByRole('searchbox'); fireEvent.change(search, { target: { value: 'blue accent' } });
    const result = screen.getByRole('button', { name: 'Blue accent — Appearance' }); expect(result).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Toggle saving' })); await waitFor(() => expect(result).toBeDisabled());
    fireEvent.keyDown(search, { key: 'Enter' }); expect(search).toHaveValue('blue accent');
    fireEvent.click(screen.getByRole('button', { name: 'Toggle saving' })); await waitFor(() => expect(result).toBeEnabled());
    fireEvent.click(result); expect(screen.getByRole('button', { name: 'Blue accent' })).toHaveFocus();
  });
  it('bounds the initial results and lets keyboard users reveal every match', async () => {
    function Form() { const form = useRef<HTMLFormElement>(null); return <form ref={form}><SettingsSearch form={form} />{Array.from({ length: 25 }, (_, index) => <label key={index}>Setting {index}<input /></label>)}</form>; }
    render(<Form />); fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'setting' } });
    const results = screen.getByRole('list', { name: 'Settings search results' }); expect(within(results).getAllByRole('button')).toHaveLength(20);
    fireEvent.click(screen.getByRole('button', { name: 'Show more settings' }));
    await waitFor(() => expect(within(results).getAllByRole('button')).toHaveLength(25));
  });
});
