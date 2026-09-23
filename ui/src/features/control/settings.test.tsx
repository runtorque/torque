import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import defaults from './settingsContract.fixture.json';
import { settingFields } from './settingsFields';
import { StructuredSettings } from './StructuredSettings';
import { NumericSettingInput, type NumericDraft } from './NumericSettingInput';
import { reconcileSettings, changedSettings, editableSettings, resetSettings } from './settingsModel';

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
      if (key === 'enabled_events' || key === 'architect_enabled_events') { expect(within(screen.getByRole('group', { name: label! })).queryAllByRole('checkbox', { checked: true })).toHaveLength((value as string[]).length); continue; }
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

it('allows clearing and replacing a numeric draft without zero coercion and resets the mounted field', () => {
  function Form() { const [value, setValue] = useState({ max_agents: 5 }); return <StructuredSettings value={value} onChange={(next) => setValue(next as typeof value)} defaults={{ max_agents: 5 }} />; }
  render(<Form />); const input = screen.getByRole('spinbutton', { name: 'Max agents' });
  fireEvent.change(input, { target: { value: '' } }); expect(input).toHaveValue(null); expect(input).toBeInvalid();
  fireEvent.click(screen.getByRole('button', { name: 'Reset Max agents' })); expect(screen.getByRole('spinbutton', { name: 'Max agents' })).toBe(input); expect(input).toHaveValue(5);
  fireEvent.change(input, { target: { value: '0' } }); expect(input).toHaveValue(0); expect(input).toBeValid();
  fireEvent.change(input, { target: { value: '101' } }); expect(input).toBeInvalid();
  fireEvent.change(input, { target: { value: '2.5' } }); expect(input).toBeInvalid();
});
it('retains fractional editing buffers and rejects unsafe integers instead of rounding them', () => {
  function Form() { const [value, setValue] = useState<NumericDraft>(1); return <NumericSettingInput aria-label="Integer" fieldKey="max_pipeline_depth" value={value} onChange={setValue} />; }
  render(<Form />); const input = screen.getByRole('spinbutton', { name: 'Integer' });
  fireEvent.change(input, { target: { value: '9007199254740993' } }); expect(input).toBeInvalid(); expect(input).toHaveAttribute('required');
  fireEvent.change(input, { target: { value: '7' } }); expect(input).toBeValid(); expect(input).toHaveValue(7);
});
it('treats user map keys as literal strings even when their names match numeric setting fields', () => {
  const change = vi.fn(); render(<StructuredSettings value={{ env_vars: { max_agents: 'arbitrary' } }} onChange={change} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Env vars: max_agents' }), { target: { value: '009' } });
  expect(change).toHaveBeenCalledWith({ env_vars: { max_agents: '009' } });
});

it('reconciles nested local edits and removals without dropping remote additions', () => {
  const before = { number: 1, map: { edited: 'old', untouched: 'old', removed: 'old' }, list: ['old'] };
  const draft: Record<string, unknown> = { number: '', map: { edited: 'draft', untouched: 'old' }, list: ['old'] };
  const latest = { number: 9, map: { edited: 'remote', untouched: 'fresh', removed: 'remote', added: 'new' }, list: ['fresh'] };
  expect(reconcileSettings(before, draft, latest)).toEqual({ number: '', map: { edited: 'draft', untouched: 'fresh', added: 'new' }, list: ['fresh'] });
});

it('offers only the three MCP capture modes and explains future-event capture and allowlist behavior', () => {
  const change = vi.fn();
  render(<StructuredSettings value={{ mcp_call_log_args_capture: 'metadata', mcp_call_log_full_capture_tools: [] }} onChange={change} />);
  const capture = screen.getByRole('combobox', { name: 'MCP call log args capture' });
  expect([...capture.querySelectorAll('option')].map((option) => [option.value, option.textContent])).toEqual([['off', 'Off'], ['metadata', 'Metadata'], ['full', 'Full']]);
  expect(capture).toHaveAccessibleDescription(/future events/i);
  expect(screen.getByRole('textbox', { name: 'MCP call log full capture tools' })).toHaveAccessibleDescription(/metadata mode/i);
  fireEvent.change(capture, { target: { value: 'off' } });
  expect(change).toHaveBeenCalledWith({ mcp_call_log_args_capture: 'off', mcp_call_log_full_capture_tools: [] });
});
it.each([
  ['event_ingest_max_rows', 'Event ingest max rows', 1, ['0', '-1', '1.5', '']],
  ['event_ingest_max_days', 'Event ingest max days', 0, ['-1', '0.5', '']],
] as const)('enforces the %s retention boundary without changing invalid drafts', (key, label, minimum, invalid) => {
  function Form() { const [value, setValue] = useState<Record<string, unknown>>({ [key]: minimum }); return <StructuredSettings value={value} onChange={setValue} />; }
  render(<Form />); const input = screen.getByRole('spinbutton', { name: label });
  expect(input).toHaveAttribute('min', String(minimum)); expect(input).toBeValid();
  for (const draft of invalid) { fireEvent.change(input, { target: { value: draft } }); expect(input).toBeInvalid(); expect(input).toHaveValue(draft ? Number(draft) : null); }
  fireEvent.change(input, { target: { value: String(minimum) } }); expect(input).toBeValid();
});

it.each([
  ['guidance_hint_cadence', 'Guidance hint cadence', 0, 100],
  ['context_default_ttl_days', 'Context default ttl days', 1, 60],
  ['perceived_empty_probe_threshold', 'Perceived empty probe threshold', 2, 25],
  ['perceived_empty_window_seconds', 'Perceived empty window seconds', 10, 3600],
] as const)('keeps %s drafts within the daemon-supported range', (key, label, minimum, maximum) => {
  function Form() { const [value, setValue] = useState<Record<string, unknown>>({ [key]: minimum }); return <StructuredSettings value={value} onChange={setValue} />; }
  render(<Form />); const input = screen.getByRole('spinbutton', { name: label });
  for (const valid of [minimum, maximum]) { fireEvent.change(input, { target: { value: String(valid) } }); expect(input).toBeValid(); }
  for (const draft of ['', String(minimum - 1), String(maximum + 1), String(minimum + 0.5)]) {
    fireEvent.change(input, { target: { value: draft } }); expect(input).toBeInvalid(); expect(input).toHaveValue(draft ? Number(draft) : null);
  }
});
