import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ModalDialog } from './primitives';
function Draft() { const [value, setValue] = useState(''); return <label>Description<input value={value} onChange={(event) => setValue(event.target.value)} /></label>; }
it('keeps an explicit accessible title through child edits and parent title changes without replacing the draft', () => {
  const { rerender } = render(<ModalDialog title="Task details" isOpen><Draft /></ModalDialog>);
  const dialog = screen.getByRole('dialog', { name: 'Task details' }); const draft = screen.getByLabelText('Description');
  fireEvent.change(draft, { target: { value: 'Retained text' } }); draft.focus();
  expect(screen.getByRole('dialog', { name: 'Task details' })).toBe(dialog); expect(dialog).toHaveAttribute('aria-label', 'Task details');
  rerender(<ModalDialog title="Updated task" isOpen><Draft /></ModalDialog>);
  expect(screen.getByRole('dialog', { name: 'Updated task' })).toBe(dialog); expect(screen.getByRole('heading', { name: 'Updated task' })).toBeVisible();
  expect(screen.getByLabelText('Description')).toBe(draft); expect(draft).toHaveValue('Retained text'); expect(draft).toHaveFocus();
});
