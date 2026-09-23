import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it } from 'vitest';
import type { UnknownRecord } from '../../protocol';
import { EngineerNotificationPreset } from './EngineerNotificationPreset';
import { engineerNotificationPresets, matchNotificationPreset, notificationPresetValues } from './engineerNotificationPresets';
it('exports only preset settings and independent event arrays', () => {
  for (const name of Object.keys(engineerNotificationPresets) as (keyof typeof engineerNotificationPresets)[]) {
    const values = notificationPresetValues(name);
    expect(Object.keys(values)).toHaveLength(5); expect(matchNotificationPreset({ ...values })).toBe(name);
    values.enabled_events.push('operator-added'); expect(notificationPresetValues(name).enabled_events).not.toContain('operator-added');
  }
});
it('recognizes string/number and event-order equivalents while preserving unknown/custom/invalid values', () => {
  const quiet = notificationPresetValues('quiet');
  expect(matchNotificationPreset({ ...quiet, heartbeat_interval: '0', enabled_events: 'task_health_alert, task_derived, task_derived' })).toBe('quiet');
  for (const changed of [{ heartbeat_interval: '' }, { push_interval: '120.5' }, { max_interval: '600suffix' }, { enabled_events: [...quiet.enabled_events, 'custom_event'] }, { enabled_events: [] }, { enabled_events: [{ name: 'task_derived' }] }]) {
    expect(matchNotificationPreset({ ...quiet, ...changed })).toBe('custom');
  }
});
it('applies one bundle, keeps unrelated edits, and reflects manual edits as Custom without rewriting them', () => {
  function Form() { const [value, setValue] = useState<UnknownRecord>({ ...notificationPresetValues('normal'), custom_instructions: 'Keep this', paused: true }); return <><EngineerNotificationPreset value={value} onApply={(preset) => setValue((old) => ({ ...old, ...preset }))} /><label>Heartbeat<input value={typeof value.heartbeat_interval === 'number' || typeof value.heartbeat_interval === 'string' ? value.heartbeat_interval : ''} onChange={(event) => setValue({ ...value, heartbeat_interval: event.target.value })} /></label><output aria-label="Draft">{JSON.stringify(value)}</output></>; }
  render(<Form />); const picker = screen.getByRole('combobox', { name: 'Engineer notification preset' }); expect(picker).toHaveValue('normal');
  fireEvent.change(picker, { target: { value: 'quiet' } }); expect(screen.getByLabelText('Heartbeat')).toHaveValue('0');
  expect(JSON.parse(screen.getByLabelText('Draft').textContent)).toMatchObject({ ...notificationPresetValues('quiet'), custom_instructions: 'Keep this', paused: true });
  fireEvent.change(screen.getByLabelText('Heartbeat'), { target: { value: '17' } }); expect(picker).toHaveValue('custom');
  fireEvent.change(picker, { target: { value: 'custom' } }); expect(screen.getByLabelText('Heartbeat')).toHaveValue('17');
  fireEvent.change(picker, { target: { value: 'noisy' } }); expect(picker).toHaveValue('noisy'); expect(screen.getByLabelText('Heartbeat')).toHaveValue('60');
});
