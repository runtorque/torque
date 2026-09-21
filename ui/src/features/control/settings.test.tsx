import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import defaults from './settingsContract.fixture.json';
import { settingFields } from './settingsFields';
import { StructuredSettings } from './StructuredSettings';
import { changedSettings, editableSettings, resetSettings } from './settingsModel';

describe('settings reset contracts', () => {
  it('filters runtime identities, preserves nested value types and ignores object key ordering', () => {
    expect(editableSettings({ group: 'g', engineer_agent_id: 'e', pending_note: 'keep', shell: '/bin/zsh' })).toEqual({ shell: '/bin/zsh' });
    expect(changedSettings({ map: { a: 1, b: 2 }, enabled: true }, { map: { b: 2, a: 1 }, enabled: false })).toEqual({ enabled: false });
    const defaults = { env_vars: {}, worker_provider: '', max_agents: 0, engineer_agent_id: '' };
    expect(resetSettings({ max_agents: 5, engineer_agent_id: 'e' }, defaults)).toEqual({ env_vars: {}, worker_provider: '', max_agents: 0, engineer_agent_id: 'e' });
  });
  it('resets list buffers without remounting or losing subsequent trailing newlines', () => {
    function Form() { const [value, setValue] = useState({ worktree_symlinks: ['old'], env_vars: { CUSTOM: 'old' } }); return <StructuredSettings value={value} onChange={(next) => setValue(next as typeof value)} defaults={{ worktree_symlinks: [], env_vars: {} }} />; }
    render(<Form />);
    const list = screen.getByRole('textbox', { name: 'Worktree symlinks' });
    fireEvent.change(list, { target: { value: 'draft\n' } }); expect(list).toHaveValue('draft\n');
    fireEvent.click(screen.getByRole('button', { name: 'Reset Worktree symlinks' })); expect(list).toHaveValue('');
    fireEvent.change(list, { target: { value: 'next\n' } }); expect(list).toHaveValue('next\n');
    fireEvent.click(screen.getByRole('button', { name: 'Reset Env vars' })); expect(screen.queryByRole('textbox', { name: 'Env vars: CUSTOM' })).not.toBeInTheDocument();
  });
  it('exposes typed GitHub controls for an empty map without promoting untouched defaults', () => {
    const change = vi.fn(); render(<StructuredSettings value={{ board_sync_github: {} }} onChange={change} />);
    fireEvent.change(screen.getByLabelText('Board sync github: Github close issues via pr'), { target: { value: 'false' } });
    expect(change).toHaveBeenCalledWith({ board_sync_github: { github_close_issues_via_pr: false } });
    expect(screen.getByLabelText('Board sync github: GitHub project number')).toHaveAttribute('type', 'number');
  });
  it.each(Object.entries(defaults))('renders every editable %s schema field using its declared value type', (_scope, values) => {
    const fields = editableSettings(values);
    const view = render(<StructuredSettings value={fields} onChange={() => undefined} />);
    for (const [key, value] of Object.entries(fields)) {
      const label = settingFields[key]?.label; expect(label, key).toBeTruthy();
      if (value && typeof value === 'object' && !Array.isArray(value)) continue;
      const control = screen.getByLabelText(label!);
      if (typeof value === 'boolean') expect(control).toHaveValue(String(value));
      else if (typeof value === 'number') expect(control).toHaveValue(control.tagName === 'SELECT' ? String(value) : value);
      else if (Array.isArray(value)) expect(control).toHaveValue(value.join('\n'));
      else expect(control).toHaveValue(String(value));
    }
    view.unmount();
  });
  it('keeps numeric select values numeric and uses integer input steps', () => {
    const change = vi.fn();
    render(<StructuredSettings value={{ push_interval: 60, max_agents: 2 }} onChange={change} />);
    const interval = screen.getByLabelText('Push interval');
    expect(interval.tagName).toBe('SELECT'); fireEvent.change(interval, { target: { value: '120' } }); expect(change).toHaveBeenCalledWith({ push_interval: 120, max_agents: 2 });
    expect(screen.getByLabelText('Max agents')).toHaveAttribute('step', '1');
  });
});
