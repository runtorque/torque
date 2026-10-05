import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { StructuredSettings } from './StructuredSettings';

describe('Architect journal checkpoint setting', () => {
  function Form({ initial = 'every_10_actions' }: { initial?: string }) { const [value, setValue] = useState({ architect_journal_checkpoint_frequency: initial }); return <form aria-label="Settings"><StructuredSettings value={value} defaults={{ architect_journal_checkpoint_frequency: 'every_10_actions' }} onChange={(next) => setValue(next as typeof value)} /></form>; }
  it('offers named action, minute and manual choices, and keeps the focused select on draft edits', () => {
    render(<Form />); const choice = screen.getByRole('combobox', { name: 'Architect journal checkpoint frequency' });
    expect(choice).toHaveValue('every_10_actions'); expect(screen.getByRole('option', { name: 'Every 5 actions' })).toBeInTheDocument(); expect(screen.getByRole('option', { name: 'Every 60 minutes' })).toBeInTheDocument();
    act(() => choice.focus()); fireEvent.change(choice, { target: { value: 'manual_only' } }); expect(choice).toHaveFocus(); expect(choice).toHaveValue('manual_only'); expect(screen.getByRole('option', { name: 'Manual only' })).toBeInTheDocument();
  });
  it('retains custom frequency editing with validation and resets to the daemon default', () => {
    render(<Form initial="every_7_minutes" />); const choice = screen.getByRole('combobox', { name: 'Architect journal checkpoint frequency' });
    expect(screen.getByRole('option', { name: 'Custom · Every 7 minutes' })).toBeInTheDocument(); const custom = screen.getByRole('textbox', { name: 'Custom journal checkpoint frequency' }); expect(custom).toHaveValue('every_7_minutes');
    act(() => custom.focus()); fireEvent.change(custom, { target: { value: 'every_20_minutes' } }); expect(custom).toHaveFocus(); expect(custom).toHaveValue('every_20_minutes');
    fireEvent.change(custom, { target: { value: 'every_0_actions' } }); expect(custom).toBeInvalid();
    fireEvent.change(custom, { target: { value: 'every_1_minutes' } }); expect(custom).toBeValid(); expect(screen.getByRole('option', { name: 'Custom · Every 1 minute' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reset Architect journal checkpoint frequency' })); expect(choice).toHaveValue('every_10_actions'); expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    fireEvent.change(choice, { target: { value: '__custom' } }); const entry = screen.getByRole('textbox', { name: 'Custom journal checkpoint frequency' }); expect(entry).toBeInvalid(); fireEvent.change(entry, { target: { value: 'every_35_actions' } }); expect(entry).toBeValid();
  });
});
