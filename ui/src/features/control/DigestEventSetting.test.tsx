import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { StructuredSettings } from './StructuredSettings';

describe('digest event settings', () => {
  it('offers Engineer optional events, shows the mandatory floor, and preserves extension events', () => {
    const change = vi.fn(); render(<StructuredSettings value={{ enabled_events: ['agent_started', 'extension_event'] }} onChange={change} />);
    const group = screen.getByRole('group', { name: 'Enabled events' });
    expect(within(group).getByRole('checkbox', { name: 'Agent started' })).toBeChecked();
    const mandatory = within(group).getByRole('list', { name: 'Events always included in Engineer digests' });
    expect(mandatory).toHaveTextContent('Task completed'); expect(mandatory).toHaveTextContent('Task verification updated'); expect(mandatory).toHaveTextContent('Worker boot failed');
    expect(within(group).queryByRole('checkbox', { name: 'Agent error' })).not.toBeInTheDocument();
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Task dispatched' }));
    expect(change).toHaveBeenCalledWith({ enabled_events: ['agent_started', 'extension_event', 'task_dispatched'] });
  });
  it('offers Architect-specific events and keeps mandatory events out of editable choices', () => {
    const change = vi.fn(); render(<StructuredSettings value={{ architect_enabled_events: ['task_done'] }} onChange={change} />);
    const group = screen.getByRole('group', { name: 'Architect enabled events' });
    expect(within(group).getByRole('checkbox', { name: 'Task done' })).toBeChecked();
    expect(within(group).getByRole('list', { name: 'Events always included in Architect digests' })).toHaveTextContent('Engineer awaiting human input');
    expect(within(group).queryByRole('checkbox', { name: 'Task blocked' })).not.toBeInTheDocument();
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Engineer queue empty' })); expect(change).toHaveBeenCalledWith({ architect_enabled_events: ['task_done', 'engineer_queue_empty'] });
  });
  it('allows clearing optional events and resetting without losing checkbox identity or extension choices', () => {
    function Form() { const [value, setValue] = useState({ enabled_events: ['agent_started', 'extension_event'] }); return <StructuredSettings value={value} onChange={(next) => setValue(next as typeof value)} defaults={{ enabled_events: [] }} />; }
    render(<Form />); const input = screen.getByRole('checkbox', { name: 'Agent started' }); act(() => input.focus()); fireEvent.click(input); expect(input).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Agent started' })).toBe(input); expect(input).toHaveFocus();
    const extension = screen.getByRole('checkbox', { name: 'Extension event' }); fireEvent.click(extension); expect(extension).not.toBeChecked(); fireEvent.click(extension); expect(extension).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Reset Enabled events' })); expect(input).not.toBeChecked(); expect(extension).not.toBeChecked();
  });
});

it('adds custom names without submitting Settings or allowing mandatory/duplicate entries', () => {
  const submit = vi.fn();
  function Form() { const [value, setValue] = useState({ enabled_events: ['agent_started'] }); return <form onSubmit={submit}><StructuredSettings value={value} onChange={(next) => setValue(next as typeof value)} /></form>; }
  render(<Form />); const input = screen.getByLabelText('Additional Engineer event name');
  fireEvent.change(input, { target: { value: 'task_completed' } }); expect(screen.getByRole('button', { name: 'Add Engineer event' })).toBeDisabled(); expect(screen.getByText('This event is always included.')).toBeVisible();
  fireEvent.change(input, { target: { value: 'agent_started' } }); expect(screen.getByRole('button', { name: 'Add Engineer event' })).toBeDisabled();
  fireEvent.change(input, { target: { value: '  extension_custom  ' } }); fireEvent.keyDown(input, { key: 'Enter' });
  expect(screen.getByRole('checkbox', { name: 'Extension custom' })).toBeChecked(); expect(input).toHaveValue(''); expect(submit).not.toHaveBeenCalled();
});
