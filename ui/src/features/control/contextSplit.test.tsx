import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand } from '../../protocol';
import { ContextSplit } from './ContextSplit';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function setup(initial: unknown = .4) {
  let measure = () => {}; let width = 1000; let failure = false; let mismatch = false; let hold = false;
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { measure = callback; } observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ left: 100, width, x: 100, y: 0, top: 0, right: 100 + width, bottom: 500, height: 500, toJSON: () => ({}) }));
  const requests: { command: TorqueCommand; signal: AbortSignal }[] = []; const pending: (() => void)[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand;
    requests.push({ command, signal: options?.signal as AbortSignal });
    const response = () => ({ ok: true, json: () => Promise.resolve({ ok: true, data: failure ? { type: 'error', message: 'Injected pane refusal' } : { type: 'state', context_panel_split_ratio: mismatch ? .3 : command.ratio } }) });
    return hold ? new Promise((resolve) => pending.push(() => resolve(response()))) : Promise.resolve(response());
  }));
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, context_panel_split_ratio: initial }));
  const view = render(<Provider store={store}><ContextSplit list={<aside aria-label="Context list">Selected entry</aside>} detail={<textarea aria-label="Draft" defaultValue="Local draft" />} /></Provider>);
  const separator = () => screen.getByRole('separator', { name: 'Resize Context panes' });
  return { ...view, store, separator, requests, fail: (value: boolean) => { failure = value; }, mismatch: (value: boolean) => { mismatch = value; }, hold: (value = true) => { hold = value; }, release: async () => { await act(async () => { pending.shift()!(); await Promise.resolve(); }); }, resize: (next: number) => { width = next; act(() => measure()); }, remote: (ratio: number) => { act(() => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, context_panel_split_ratio: ratio })); }); } };
}

it('restores saved width, supports bounded keyboard changes and follows external updates after acknowledgement', async () => {
  const test = setup(); const separator = test.separator(); expect(separator).toHaveAttribute('aria-valuenow', '40');
  fireEvent.keyDown(separator, { key: 'ArrowRight' }); await waitFor(() => expect(separator).not.toHaveAttribute('aria-valuetext', expect.stringContaining('saving')));
  expect(test.requests[0]?.command.cmd).toBe('ui_set_context_panel_split'); expect(test.requests[0]?.command.ratio).toBeCloseTo(.42);
  fireEvent.keyDown(separator, { key: 'End' }); await waitFor(() => expect(separator).toHaveAttribute('aria-valuetext', '62% list width'));
  fireEvent.keyDown(separator, { key: 'Home' }); await waitFor(() => expect(separator).toHaveAttribute('aria-valuetext', '28% list width'));
  test.remote(.51); expect(separator).toHaveAttribute('aria-valuenow', '51');
});

it('previews pointer changes, commits once, retains draft/caret and cancels without writing', async () => {
  const test = setup(); const separator = test.separator(); const draft = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Draft' }); draft.focus(); draft.setSelectionRange(1, 5);
  fireEvent.pointerDown(separator, { button: 0, pointerId: 2, clientX: 500 }); fireEvent.pointerMove(separator, { pointerId: 2, clientX: 650 }); expect(separator).toHaveAttribute('aria-valuenow', '55'); expect(test.requests).toHaveLength(0);
  fireEvent.pointerUp(separator, { pointerId: 2, clientX: 650 }); await waitFor(() => expect(separator).toHaveAttribute('aria-valuetext', '55% list width')); expect(test.requests).toHaveLength(1);
  expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft); expect(draft).toHaveFocus(); expect([draft.selectionStart, draft.selectionEnd]).toEqual([1, 5]);
  fireEvent.pointerDown(separator, { button: 0, pointerId: 3, clientX: 650 }); fireEvent.pointerMove(separator, { pointerId: 3, clientX: 900 }); expect(separator).toHaveAttribute('aria-valuenow', '62'); fireEvent.pointerCancel(separator, { pointerId: 3 }); expect(separator).toHaveAttribute('aria-valuenow', '55'); expect(test.requests).toHaveLength(1);
});

it('serializes rapid commits and saves the latest queued width without an older acknowledgement resetting it', async () => {
  const test = setup(); test.hold(); const separator = test.separator();
  fireEvent.keyDown(separator, { key: 'ArrowRight' }); fireEvent.keyDown(separator, { key: 'ArrowRight' }); fireEvent.keyDown(separator, { key: 'ArrowRight' });
  expect(test.requests).toHaveLength(1); expect(separator).toHaveAttribute('aria-valuenow', '46'); test.remote(.42); expect(separator).toHaveAttribute('aria-valuenow', '46');
  await test.release(); expect(test.requests).toHaveLength(2); expect(Number(test.requests[1]?.command.ratio)).toBeCloseTo(.46); await test.release(); expect(separator).toHaveAttribute('aria-valuetext', '46% list width');
});

it('keeps an unsaved width across failed reads/snapshots and retries a mismatched acknowledgement', async () => {
  const test = setup(); test.fail(true); fireEvent.keyDown(test.separator(), { key: 'End' }); await screen.findByRole('alert'); test.remote(.35); expect(test.separator()).toHaveAttribute('aria-valuenow', '62');
  test.fail(false); test.mismatch(true); fireEvent.click(screen.getByRole('button', { name: 'Retry pane width' })); await screen.findByText(/not acknowledged/); expect(test.separator()).toHaveAttribute('aria-valuenow', '62');
  test.mismatch(false); fireEvent.click(screen.getByRole('button', { name: 'Retry pane width' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(test.requests.map((request) => request.command.ratio)).toEqual([.62, .62, .62]);
});

it('hides compact resizing without losing the saved wide ratio or editor and aborts pending persistence on unmount', async () => {
  const test = setup(.5); const draft = screen.getByRole('textbox', { name: 'Draft' }); const separator = test.separator();
  test.resize(700); expect(screen.queryByRole('separator')).not.toBeInTheDocument(); fireEvent.keyDown(separator, { key: 'End' }); expect(test.requests).toHaveLength(0); expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft);
  test.resize(1000); expect(test.separator()).toHaveAttribute('aria-valuenow', '50'); test.hold(); fireEvent.keyDown(separator, { key: 'End' }); test.unmount(); expect(test.requests.at(-1)?.signal.aborted).toBe(true); await test.release(); expect(test.requests).toHaveLength(1);
});

it('accepts later remote widths after cancelling a preview whose earlier save finished during the drag', async () => {
  const test = setup(); test.hold(); const separator = test.separator();
  fireEvent.keyDown(separator, { key: 'ArrowRight' });
  fireEvent.pointerDown(separator, { button: 0, pointerId: 2, clientX: 520 }); fireEvent.pointerMove(separator, { pointerId: 2, clientX: 650 });
  await test.release(); expect(separator).toHaveAttribute('aria-valuenow', '55');
  fireEvent.pointerCancel(separator, { pointerId: 2 }); expect(separator).toHaveAttribute('aria-valuenow', '42');
  test.remote(.51); expect(separator).toHaveAttribute('aria-valuenow', '51'); expect(test.requests).toHaveLength(1);
});


it('bounds a lost width acknowledgement, preserves the latest ratio and draft, and retries explicitly', async () => {
  vi.useFakeTimers(); const test = setup(); test.hold(); const separator = test.separator(); const draft = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Draft' }); draft.focus(); draft.setSelectionRange(2, 7);
  fireEvent.keyDown(separator, { key: 'ArrowRight' }); fireEvent.keyDown(separator, { key: 'ArrowRight' }); fireEvent.keyDown(separator, { key: 'ArrowRight' }); expect(test.requests).toHaveLength(1);
  await act(() => vi.advanceTimersByTimeAsync(30_001)); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(separator).toHaveAttribute('aria-valuetext', '46% list width'); expect(test.requests[0]?.signal.aborted).toBe(true); expect(test.requests).toHaveLength(1); expect(draft).toHaveFocus(); expect([draft.selectionStart, draft.selectionEnd]).toEqual([2, 7]);
  test.remote(.42); await test.release(); expect(separator).toHaveAttribute('aria-valuenow', '46'); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(test.requests).toHaveLength(1);
  test.hold(false); await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry pane width' })); await Promise.resolve(); }); expect(test.requests).toHaveLength(2); expect(Number(test.requests[1]?.command.ratio)).toBeCloseTo(.46); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); test.remote(.51); expect(separator).toHaveAttribute('aria-valuenow', '51'); expect(draft).toHaveValue('Local draft');
});
it('stops the queued width after a refused acknowledgement and does not replay when hidden', async () => {
  const test = setup(); test.hold(); test.fail(true); fireEvent.keyDown(test.separator(), { key: 'ArrowRight' }); fireEvent.keyDown(test.separator(), { key: 'End' }); await test.release(); expect(screen.getByRole('alert')).toHaveTextContent('Injected pane refusal'); expect(test.requests).toHaveLength(1); expect(test.separator()).toHaveAttribute('aria-valuetext', '62% list width');
  test.fail(false); fireEvent.click(screen.getByRole('button', { name: 'Retry pane width' })); expect(test.requests).toHaveLength(2); test.unmount(); expect(test.requests[1]?.signal.aborted).toBe(true); await test.release(); expect(test.requests).toHaveLength(2);
});
