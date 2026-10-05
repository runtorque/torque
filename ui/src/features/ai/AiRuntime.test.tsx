import { act, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { expect, it } from 'vitest';
import { createAppStore, projectionActions, selectOperationsState } from '../../app/store';
import { useAppSelector } from '../../app/hooks';
import { AiRuntime } from './AiRuntime';
import { aiCount, aiIndexControl, aiTime } from './aiRuntimeModel';
const settings = { embeddings: { runtime: 'fastembed', model_id: 'desired', active_model_id: 'active', active_dims: 768, dependency: { status: 'missing', packages: ['sqlite-vec', 'fastembed'], install_hint: 'make ai-deps' } }, index: { status: 'rebuild_pending', counts: { sources: 2, chunks: 12, indexed: 4, pending: 8, stale: 3, errors: 1 }, last_built_at: 1700000000, last_error: '<script>index error</script>', rebuild_warning: { required: true, reason: 'Model changed' }, current_job: { id: 'job-1', status: 'error', mode: 'rebuild', message: 'Re-embed entries', error: 'Index unavailable' } }, boot_summary: { enabled: false, status: 'stale', counts: { ready: 3, stale: 4, errors: 5 }, last_refreshed_at: 1700000000000, last_error: 'Summary unavailable' }, metering: { calls_24h: 6, input_tokens_24h: 7, output_tokens_24h: 8, cache_read_input_tokens_24h: 9, last_call_at: 1700000000 } };
function Connected() { return <AiRuntime settings={useAppSelector(selectOperationsState).aiSettings} />; }
it('renders all Classic dependency, index, job, summary and metering fields safely', () => {
  const store = createAppStore(); store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'ai_settings', settings })); render(<Provider store={store}><Connected /></Provider>);
  const dependency = within(screen.getByRole('region', { name: 'Embedding dependencies' })); expect(dependency.getByText('make ai-deps')).toBeVisible(); expect(dependency.getByText(/GB-scale/)).toBeVisible(); expect(dependency.getByText(/sqlite-vec, fastembed/)).toBeVisible();
  const index = within(screen.getByRole('region', { name: 'Vector index' })); for (const text of ['Sources 2', 'Chunks 12', 'Indexed 4', 'Pending 8', 'Stale 3', 'Errors 1', 'active · 768 dims', 'desired', 'Model changed', 'job-1', 'Re-embed entries', 'Index unavailable', '<script>index error</script>']) expect(index.getByText(text)).toBeVisible(); expect(document.querySelector('script')).toBeNull(); expect(index.getByRole('button', { name: 'Rebuild index' })).toBeDisabled();
  const summary = within(screen.getByRole('region', { name: 'Boot summaries and metering' })); for (const text of ['Ready 3', 'Stale 4', 'Errors 5', 'Calls 6', 'Input 7', 'Output 8', 'Cache read 9', 'Summary unavailable']) expect(summary.getByText(text)).toBeVisible(); expect(summary.getByText(/disabled in settings/)).toBeVisible(); expect(document.querySelectorAll('time[datetime="2023-11-14T22:13:20.000Z"]')).toHaveLength(3);
});
it('updates live diagnostics in place and suppresses obsolete ready-summary errors', () => {
  const store = createAppStore(); store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'ai_settings', settings })); render(<Provider store={store}><Connected /></Provider>);
  const model = screen.getByText('active · 768 dims'); model.tabIndex = 0; model.focus();
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 1, ops: [{ op: 'ai_index_status_update', index: { counts: { pending: 0 }, current_job: null }, embeddings: { active_model_id: 'new-active' } }, { op: 'ai_summary_status_update', boot_summary: { status: 'ready' }, metering: { calls_24h: 14 } }] })); });
  expect(screen.getByText('new-active · 768 dims')).toBe(model); expect(model).toHaveFocus(); expect(screen.getByText('Pending 0')).toBeVisible(); expect(screen.queryByText('job-1')).not.toBeInTheDocument(); expect(screen.queryByText('Summary unavailable')).not.toBeInTheDocument(); expect(screen.getByText('Calls 14')).toBeVisible();
});
it('formats missing, malformed, negative and extreme counts/times without throwing', () => {
  for (const value of [null, undefined, 'bad', -4, Infinity, {}, []]) { expect(aiCount(value)).toBe(0); expect(aiTime(value)).toBeNull(); }
  expect(aiCount('3.9')).toBe(3); expect(aiTime(1e20)).toBeNull(); expect(aiTime(1700000000)).toBe(aiTime(1700000000000));
  const store = createAppStore(); render(<Provider store={store}><AiRuntime settings={{ index: { status: 'future_status' } }} /></Provider>); expect(screen.getByText('future status')).toHaveAttribute('data-tone', 'warning'); expect(screen.getAllByText('Never')).toHaveLength(3);
});
it.each([{ index: { status: 'ready' } }, { index: { status: 'rebuild_pending' } }, { index: { counts: { chunks: 1 } } }, { index: { counts: { indexed: 1 } } }, { index: { rebuild_warning: { required: true } } }])('selects Classic rebuild mode for %j', (value) => { expect(aiIndexControl(value).mode).toBe('rebuild'); });
it.each([{ index: { status: 'building' } }, { index: { current_job: { status: 'queued' } } }, { index: { current_job: { status: 'running' } } }, { embeddings: { dependency: { status: 'missing' } } }])('gates starts for %j', (value) => { expect(aiIndexControl(value).blocked).toBe(true); });
it('keeps empty-index starts incremental without inventing an enabled-AI gate', () => { expect(aiIndexControl({ enabled: false, index: { status: 'disabled' } })).toMatchObject({ mode: 'incremental', blocked: false }); });
