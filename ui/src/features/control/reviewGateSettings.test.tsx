import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { UnknownRecord } from '../../protocol';
import { StructuredSettings } from './StructuredSettings';
const thresholds = { ship_direct_max: 50, review_default_above: 150, self_review_bypass_allowed: false };
function Form() {
  const [value, setValue] = useState<UnknownRecord>({ architect_review_gate_thresholds: thresholds, env_vars: { ship_direct_max: '009' } });
  return <><StructuredSettings value={value} onChange={setValue} defaults={{ architect_review_gate_thresholds: thresholds }} /><output aria-label="Draft">{JSON.stringify(value)}</output></>;
}
function draft() { return JSON.parse(screen.getByLabelText('Draft').textContent) as { architect_review_gate_thresholds: Record<string, unknown>; env_vars: Record<string, string> }; }
describe('Architect review gate settings', () => {
  it.each(['ship_direct_max', 'review_default_above'] as const)('rejects lossy %s drafts and keeps valid zero and integer values typed', (key) => {
    render(<Form />);
    const input = screen.getByRole('spinbutton', { name: new RegExp(key.replaceAll('_', '.'), 'i') });
    for (const value of ['-1', '1.5', '9007199254740993', '']) {
      fireEvent.change(input, { target: { value } }); expect(input).toBeInvalid(); expect(screen.getByRole('spinbutton', { name: new RegExp(key.replaceAll('_', '.'), 'i') })).toBe(input);
    }
    fireEvent.change(input, { target: { value: '0' } }); expect(input).toBeValid(); expect(draft().architect_review_gate_thresholds[key]).toBe(0);
    fireEvent.change(input, { target: { value: '73' } }); expect(input).toBeValid(); expect(draft().architect_review_gate_thresholds[key]).toBe(73);
    fireEvent.click(screen.getByRole('button', { name: 'Reset Architect review gate thresholds' })); expect(input).toHaveValue(thresholds[key]);
  });
  it('exposes the fixed named threshold fields and keeps similarly named environment entries literal', () => {
    render(<Form />); const fields = screen.getByRole('group', { name: 'Architect review gate thresholds' });
    expect(within(fields).getAllByRole('spinbutton')).toHaveLength(2);
    expect(within(fields).queryByRole('button', { name: 'Add entry' })).not.toBeInTheDocument();
    expect(within(fields).queryByRole('button', { name: /^Remove / })).not.toBeInTheDocument();
    fireEvent.change(within(fields).getByRole('combobox'), { target: { value: 'true' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Env vars: ship_direct_max' }), { target: { value: '1.5' } });
    expect(draft()).toMatchObject({ architect_review_gate_thresholds: { self_review_bypass_allowed: true }, env_vars: { ship_direct_max: '1.5' } });
  });
});
