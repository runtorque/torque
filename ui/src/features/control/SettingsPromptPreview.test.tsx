import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SettingsPromptPreview } from './SettingsPromptPreview';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame, UnknownRecord } from '../../protocol';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const props = { group: 'Preview group', groupSettings: { worker_provider: 'generic' }, engineer: { custom_instructions: 'Unsaved engineer' }, architect: { custom_instructions: 'Unsaved architect' } };
const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
beforeEach(() => { read.mockReset(); });
afterEach(() => { vi.useRealTimers(); if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard); else Reflect.deleteProperty(navigator, 'clipboard'); });
function pending() {
  let resolve!: (frame: AuxiliaryFrame) => void;
  read.mockImplementationOnce(() => new Promise<AuxiliaryFrame>((complete) => { resolve = complete; }));
  return async (patch: UnknownRecord = {}, index = read.mock.calls.length - 1) => {
    const command = read.mock.calls[index]![0];
    await act(async () => { resolve({ type: 'system_prompt_preview', request_id: command.request_id, group: command.group, kind: command.kind, prompt: 'Rendered unsaved prompt', metadata: { provider: 'generic' }, ...patch }); await Promise.resolve(); });
  };
}
const preview = (kind = 'Engineer') => fireEvent.click(screen.getByRole('button', { name: `Preview ${kind} system prompt` }));
const copy = () => screen.getByRole('button', { name: 'Copy rendered prompt' });
it('renders and copies only an acknowledged preview of the current unsaved role and group', async () => {
  const complete = pending(); const write = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: write } });
  render(<SettingsPromptPreview {...props} />); preview();
  expect(read.mock.calls[0]![0]).toMatchObject({ cmd: 'preview_system_prompt', group: props.group, kind: 'engineer', group_settings: props.groupSettings, settings: props.engineer });
  expect(copy()).toBeDisabled(); expect(screen.getByRole('status')).toHaveTextContent('Rendering Engineer');
  await complete(); expect(screen.getByLabelText('Rendered system prompt')).toHaveTextContent('Rendered unsaved prompt');
  fireEvent.click(copy()); await screen.findByText('Prompt copied.'); expect(write).toHaveBeenCalledExactlyOnceWith('Rendered unsaved prompt');
});
it.each([{ request_id: 'old' }, { group: 'other' }, { kind: 'architect' }, { type: 'ok' }, { prompt: null }])('rejects a mismatched or malformed result %j', async (patch) => {
  const complete = pending(); render(<SettingsPromptPreview {...props} />); preview(); await complete(patch);
  expect(screen.getByRole('alert')).toHaveTextContent('did not match'); expect(copy()).toBeDisabled(); expect(screen.queryByLabelText('Rendered system prompt')).not.toBeInTheDocument();
});
it('cancels superseded roles and ignores their late response', async () => {
  const engineer = pending(); const architect = pending(); render(<SettingsPromptPreview {...props} />);
  preview(); const signal = read.mock.calls[0]![1]; preview('Architect'); expect(signal?.aborted).toBe(true);
  expect(read.mock.calls[1]![0]).toMatchObject({ kind: 'architect', settings: props.architect });
  await architect({ prompt: 'Architect result' }, 1); await engineer({ prompt: 'Old engineer result' }, 0);
  expect(screen.getByLabelText('Rendered system prompt')).toHaveTextContent('Architect result'); expect(screen.queryByText('Old engineer result')).not.toBeInTheDocument();
});
it.each(['group', 'groupSettings', 'engineer'] as const)('invalidates a pending read when %s changes and does not revive it on revert', async (field) => {
  const complete = pending(); const { rerender } = render(<SettingsPromptPreview {...props} />); preview();
  rerender(<SettingsPromptPreview {...props} {...{ [field]: field === 'group' ? 'Other' : { changed: true } }} />);
  expect(read.mock.calls[0]![1]?.aborted).toBe(true); rerender(<SettingsPromptPreview {...props} />); await complete();
  expect(screen.getByRole('status')).toHaveTextContent('Draft changed'); expect(copy()).toBeDisabled(); expect(screen.queryByLabelText('Rendered system prompt')).not.toBeInTheDocument();
});
it('keeps an accepted preview for an unrelated role edit but invalidates relevant edits', async () => {
  const complete = pending(); const { rerender } = render(<SettingsPromptPreview {...props} />); preview(); await complete();
  rerender(<SettingsPromptPreview {...props} architect={{ changed: true }} />); expect(copy()).toBeEnabled();
  rerender(<SettingsPromptPreview {...props} engineer={{ changed: true }} />); expect(copy()).toBeDisabled();
});
it('shows refusal, retries explicitly, and accepts an empty response', async () => {
  const failed = pending(); const success = pending(); render(<SettingsPromptPreview {...props} />); preview();
  await failed({ type: 'error', message: 'Preview refused' }, 0); expect(screen.getByRole('alert')).toHaveTextContent('Preview refused');
  fireEvent.click(screen.getByRole('button', { name: 'Retry prompt preview' })); await success({ prompt: '' }, 1);
  expect(screen.getByRole('status')).toHaveTextContent('rendered prompt is empty'); expect(copy()).toBeEnabled();
});
it('times out and aborts without automatically replaying a request', async () => {
  vi.useFakeTimers(); const complete = pending(); const { unmount } = render(<SettingsPromptPreview {...props} />); preview();
  await act(async () => { vi.advanceTimersByTime(30_000); await Promise.resolve(); }); expect(read.mock.calls[0]![1]?.aborted).toBe(true);
  expect(screen.getByRole('alert')).toHaveTextContent('timed out'); await complete(); expect(copy()).toBeDisabled(); expect(read).toHaveBeenCalledTimes(1); unmount();
});
it('aborts on unmount and disables actions during settings save', () => {
  pending(); const { rerender, unmount } = render(<SettingsPromptPreview {...props} disabled />);
  expect(screen.getByRole('button', { name: 'Preview Engineer system prompt' })).toBeDisabled(); expect(read).not.toHaveBeenCalled();
  rerender(<SettingsPromptPreview {...props} />); preview(); unmount(); expect(read.mock.calls[0]![1]?.aborted).toBe(true);
});
it('reports clipboard failure without losing the selectable prompt', async () => {
  const complete = pending(); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) } });
  render(<SettingsPromptPreview {...props} />); preview(); await complete(); fireEvent.click(copy());
  expect(await screen.findByRole('alert')).toHaveTextContent('Copy failed'); expect(screen.getByLabelText('Rendered system prompt')).toHaveAttribute('tabindex', '0'); expect(copy()).toBeEnabled();
});

it('does not let an older clipboard completion unlock or label a newer copy', async () => {
  const first = pending(); const second = pending(); let finishFirst!: () => void; let finishSecond!: () => void;
  const write = vi.fn().mockImplementationOnce(() => new Promise<void>((resolve) => { finishFirst = resolve; })).mockImplementationOnce(() => new Promise<void>((resolve) => { finishSecond = resolve; }));
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: write } });
  render(<SettingsPromptPreview {...props} />); preview(); await first({}, 0); fireEvent.click(copy());
  preview('Architect'); await second({ prompt: 'Architect copy' }, 1); fireEvent.click(copy()); expect(copy()).toBeDisabled();
  await act(async () => { finishFirst(); await Promise.resolve(); }); expect(copy()).toBeDisabled(); expect(screen.queryByText('Prompt copied.')).not.toBeInTheDocument();
  await act(async () => { finishSecond(); await Promise.resolve(); }); expect(copy()).toBeEnabled(); expect(screen.getByText('Prompt copied.')).toBeVisible(); expect(write).toHaveBeenCalledTimes(2);
});
