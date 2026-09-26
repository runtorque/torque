import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AuxiliaryFrame } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { settingsReadTimeout, settingsRequest, settingsSaveTimeout, validateSettingsAcknowledgement, validateSettingsRead } from './settingsRequests';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });
it.each([false, true])('bounds an unanswered request, aborts observation and ignores late success (writing=%s)', async (writing) => {
  let finish!: (value: AuxiliaryFrame) => void; read.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const result = settingsRequest({ cmd: 'get_global_settings' }, new AbortController().signal, writing);
  const outcome = expect(result).rejects.toThrow(writing ? 'outcome is unknown' : 'refresh timed out');
  await vi.advanceTimersByTimeAsync(writing ? settingsSaveTimeout : settingsReadTimeout); await outcome;
  expect(read.mock.calls[0]![1].aborted).toBe(true); finish({ type: 'ok' }); await Promise.resolve(); expect(read).toHaveBeenCalledTimes(1);
});
it('aborts an obsolete owner immediately and clears the deadline without waiting for its adapter', async () => {
  read.mockImplementation(() => new Promise(() => {})); const controller = new AbortController();
  const result = settingsRequest({ cmd: 'get_ai_settings' }, controller.signal); const outcome = expect(result).rejects.toMatchObject({ name: 'AbortError' }); controller.abort(); await outcome;
  expect(read.mock.calls[0]![1].aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
});
it('never starts an already aborted request and clears deadlines on normal completion/refusal', async () => {
  const controller = new AbortController(); controller.abort(); await expect(settingsRequest({ cmd: 'get_ai_settings' }, controller.signal)).rejects.toMatchObject({ name: 'AbortError' }); expect(read).not.toHaveBeenCalled();
  read.mockResolvedValueOnce({ type: 'ok' }).mockRejectedValueOnce(new Error('Refused'));
  await expect(settingsRequest({ cmd: 'engineer_update_settings' }, new AbortController().signal, true)).resolves.toEqual({ type: 'ok' }); expect(vi.getTimerCount()).toBe(0);
  await expect(settingsRequest({ cmd: 'engineer_update_settings' }, new AbortController().signal, true)).rejects.toThrow('Refused'); expect(vi.getTimerCount()).toBe(0);
});
it.each(['global', 'group', 'engineer', 'architect'])('accepts current daemon acknowledgements and rejects unrelated or mismatched %s frames', (scope) => {
  for (const frame of [{ type: 'ok' }, { type: 'state' }, { type: `${scope}_settings`, group: 'current', settings: {} }]) expect(() => validateSettingsAcknowledgement(frame, scope, 'current')).not.toThrow();
  for (const frame of [{ type: 'mission_control_summary' }, { type: `${scope}_settings`, group: 'current', settings: [] }, ...(scope === 'global' ? [] : [{ type: `${scope}_settings`, group: 'other', settings: {} }])]) expect(() => validateSettingsAcknowledgement(frame, scope, 'current')).toThrow('outcome is unknown');
});
it.each(['global_settings', 'group_settings', 'ai_settings'])('requires a settings object before accepting %s reads', (type) => {
  for (const settings of [undefined, null, [], 'wrong']) expect(() => validateSettingsRead({ type, settings }, type)).toThrow('matching settings');
  expect(() => validateSettingsRead({ type, settings: {} }, type)).not.toThrow();
});
it('validates read scope and AI write type separately from generic acknowledgements', () => {
  expect(() => validateSettingsRead({ type: 'group_settings', group: 'other', settings: {} }, 'group_settings', 'current')).toThrow();
  expect(() => validateSettingsAcknowledgement({ type: 'ok' }, 'ai', 'current')).toThrow('invalid AI');
  expect(() => validateSettingsAcknowledgement({ type: 'ai_settings', settings: {} }, 'ai', 'current')).not.toThrow();
});
