import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { SpecializationPicker } from './SpecializationPicker';
const response = (group = 'QA', names = ['frontend', 'backend', 'ui-ux']) => new Response(JSON.stringify({ ok: true, data: { type: 'specializations', group, specializations: names.map((name) => ({ name })) } }), { status: 200 });
function Form({ group = 'QA', initial = ['frontend', 'unavailable'] }: { group?: string; initial?: string[] }) { const [value, setValue] = useState(initial); return <><SpecializationPicker group={group} value={value} onChange={setValue} /><output aria-label="Draft">{JSON.stringify(value)}</output></>; }
function setup() { const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); return { store, wrapper: ({ children }: { children: React.ReactNode }) => <Provider store={store}>{children}</Provider> }; }
const draft = () => JSON.parse(screen.getByLabelText('Draft').textContent) as string[];
afterEach(() => vi.unstubAllGlobals());
it('loads on demand, excludes selected options, reorders with retained focus, and preserves unavailable selections', async () => {
  const fetcher = vi.fn(() => Promise.resolve(response())); vi.stubGlobal('fetch', fetcher); const test = setup(); render(<Form />, { wrapper: test.wrapper });
  expect(fetcher).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); await screen.findByRole('option', { name: 'backend' });
  expect(screen.queryByRole('option', { name: 'frontend' })).not.toBeInTheDocument(); expect(screen.getByText(/unavailable in this project/)).toBeVisible();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'backend' } }); fireEvent.click(screen.getByRole('button', { name: 'Add specialization' })); expect(draft()).toEqual(['frontend', 'unavailable', 'backend']); expect(screen.getByRole('combobox')).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Move backend up' })); fireEvent.click(screen.getByRole('button', { name: 'Move backend up' }));
  const rows = screen.getAllByRole('listitem'); expect(rows[0]).toHaveFocus(); expect(rows[0]).toHaveTextContent('backend · Primary'); expect(draft()).toEqual(['backend', 'frontend', 'unavailable']);
  fireEvent.click(screen.getByRole('button', { name: 'Remove backend' })); expect(screen.getAllByRole('listitem')[0]).toHaveFocus(); expect(draft()).toEqual(['frontend', 'unavailable']);
});
it('retains manual line editing, selection and focus through catalog reconnect and failed retry', async () => {
  let fail = false; vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(fail ? new Response(JSON.stringify({ ok: false, error: 'Catalog unavailable' }), { status: 503 }) : response())));
  const test = setup(); render(<Form initial={[]} />, { wrapper: test.wrapper });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); await screen.findByRole('option', { name: 'frontend' });
  fireEvent.click(screen.getByText('Edit specialization slugs')); const manual = screen.getByLabelText<HTMLTextAreaElement>('Ordered specialization slugs');
  fireEvent.change(manual, { target: { value: 'frontend\n' } }); manual.focus(); manual.setSelectionRange(2, 5);
  fail = true; act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(await screen.findByRole('alert')).toHaveTextContent('Catalog unavailable');
  expect(manual).toHaveValue('frontend\n'); expect(manual).toHaveFocus(); expect([manual.selectionStart, manual.selectionEnd]).toEqual([2, 5]); expect(draft()).toEqual(['frontend']);
  fail = false; fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(draft()).toEqual(['frontend']);
});
it('rejects foreign scope data and aborts hidden reads without replacing the selected list', async () => {
  let resolve: (value: Response) => void = () => {}; const signals: AbortSignal[] = []; let calls = 0;
  vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => { calls++; if (options.signal) signals.push(options.signal); return calls === 1 ? Promise.resolve(response('Other')) : new Promise<Response>((done) => { resolve = done; }); }));
  const test = setup(); const view = render(<Form />, { wrapper: test.wrapper });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); expect(await screen.findByRole('alert')).toHaveTextContent('did not match'); expect(draft()).toEqual(['frontend', 'unavailable']);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); view.unmount(); expect(signals.at(-1)?.aborted).toBe(true);
  await act(async () => { resolve(response()); await Promise.resolve(); }); expect(calls).toBe(2);
});
it('keeps empty and unavailable definitions distinct, and exposes duplicates only once', async () => {
  const payload: UnknownRecord = { type: 'specializations', group: 'QA', specializations: [{ name: 'frontend' }, { name: 'frontend', shadowed: true }, { name: 'backend' }, { name: 'backend' }] };
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, data: payload }), { status: 200 })).mockResolvedValueOnce(response('QA', [])); vi.stubGlobal('fetch', fetcher);
  const test = setup(); render(<Form />, { wrapper: test.wrapper }); fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); await screen.findByRole('option', { name: 'backend' });
  expect(within(screen.getByRole('combobox')).getAllByRole('option')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh specializations' })); await screen.findByText('No specialization definitions are available in this project.'); expect(draft()).toEqual(['frontend', 'unavailable']);
});
