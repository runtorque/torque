import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { StructuredSettings } from './StructuredSettings';
const templates = [
  { name: 'review', display_name: 'User review', global: true },
  { name: 'other', display_name: 'Personal helper', global: true },
  { name: 'review', display_name: 'Project reviewer', global: false },
  { name: 'shadowed', display_name: 'Hidden duplicate', global: true, shadowed: true },
];
describe('default role selection', () => {
  it('lists scoped display names with project precedence and changes only the chosen setting', () => {
    const change = vi.fn(); render(<StructuredSettings value={{ default_agent_template: '', agent_model: 'keep' }} onChange={change} templates={templates} />);
    const select = screen.getByRole('combobox', { name: 'Default agent template' }); expect(select).toHaveValue('');
    expect(within(select).getByRole('option', { name: 'None' })).toHaveValue('');
    expect(within(select).getByRole('group', { name: 'Project' })).toContainElement(within(select).getByRole('option', { name: 'Project reviewer' }));
    expect(within(select).getByRole('group', { name: 'User' })).toContainElement(within(select).getByRole('option', { name: 'Personal helper' }));
    expect(within(select).queryByRole('option', { name: 'User review' })).not.toBeInTheDocument(); expect(within(select).queryByRole('option', { name: 'Hidden duplicate' })).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'review' } }); expect(change).toHaveBeenCalledWith({ default_agent_template: 'review', agent_model: 'keep' });
  });
  it('retains unavailable selections and focus through empty catalogs, refresh and label changes', () => {
    const change = vi.fn(); const values = { default_agent_template: 'custom-role' };
    const view = render(<StructuredSettings value={values} onChange={change} templates={[]} />);
    const select = screen.getByRole('combobox', { name: 'Default agent template' }); act(() => select.focus()); expect(select).toHaveValue('custom-role');
    expect(within(select).getByRole('option', { name: 'custom-role (not in current catalog)' })).toBeVisible();
    view.rerender(<StructuredSettings value={values} onChange={change} templates={[{ name: 'custom-role', display_name: 'Now discovered', global: false }]} />);
    expect(screen.getByRole('combobox', { name: 'Default agent template' })).toBe(select); expect(select).toHaveFocus(); expect(select).toHaveValue('custom-role'); expect(within(select).getByRole('option', { name: 'Now discovered' })).toBeVisible(); expect(change).not.toHaveBeenCalled();
  });
  it('clears and resets a selected role without changing launch overrides', () => {
    function Form() { const [value, setValue] = useState({ default_agent_template: 'review', agent_model: 'custom-model' }); return <StructuredSettings value={value} onChange={(next) => setValue(next as typeof value)} defaults={{ default_agent_template: '' }} templates={templates} />; }
    render(<Form />); const select = screen.getByRole('combobox', { name: 'Default agent template' }); fireEvent.change(select, { target: { value: '' } }); expect(select).toHaveValue('');
    fireEvent.change(select, { target: { value: 'other' } }); fireEvent.click(screen.getByRole('button', { name: 'Reset Default agent template' })); expect(select).toHaveValue(''); expect(screen.getByLabelText('Agent model')).toHaveValue('custom-model');
  });
});
