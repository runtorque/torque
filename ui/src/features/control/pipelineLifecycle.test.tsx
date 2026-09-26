import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { PipelineExplorer } from './PipelineExplorer';
import type { Pipeline } from './pipelineModel';
const pipeline: Pipeline = { name: 'Build', actions: ['implement', 'review'], edges: [{ from: 'implement', to: 'review', when: 'Ready' }], asks: [] };
const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
afterEach(() => vi.unstubAllGlobals());
function harness() {
  const store = createAppStore(); const edit = vi.fn();
  const snapshot = () => { store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); };
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); snapshot();
  const pending: { command: UnknownRecord; signal: AbortSignal; resolve: (frame: UnknownRecord) => void }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve) => pending.push({ command: JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord, signal: options.signal as AbortSignal, resolve: (frame) => resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: frame }) }) }))));
  const view = render(<Provider store={store}><PipelineExplorer group="Foundation" onEdit={edit} /></Provider>);
  const reply = async (index: number, frame: UnknownRecord = { type: 'pipelines', pipelines: [pipeline] }) => { pending[index]!.resolve(frame); await settle(); };
  return { store, pending, view, snapshot, reply, edit };
}
describe('pipeline read lifecycle', () => {
  it('keeps the graph, zoom, pan, selected node and focus through a compact reconnect and failed retry', async () => {
    const h = harness(); await h.reply(0);
    const graph = screen.getByRole('group', { name: 'Pipeline graph' });
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in pipeline' })); fireEvent.keyDown(graph, { key: 'ArrowRight' });
    const node = within(graph).getByRole('button', { name: 'Edit action review' }); act(() => node.focus());
    const viewBox = graph.getAttribute('viewBox'); expect(node).toHaveAttribute('aria-pressed', 'true');
    act(() => { h.store.dispatch(connectionActions.disconnected({ at: 2 })); h.store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); h.snapshot(); }); await settle();
    expect(screen.getByRole('group', { name: 'Pipeline graph' })).toBe(graph); expect(graph).toHaveAttribute('viewBox', viewBox); expect(node).toHaveFocus(); expect(screen.getByLabelText('Pipeline')).toHaveValue('Build');
    await h.reply(1, { type: 'error', message: 'Discovery temporarily refused' });
    expect(screen.getByRole('alert')).toHaveTextContent('Discovery temporarily refused'); expect(node).toHaveFocus(); expect(graph).toHaveAttribute('viewBox', viewBox);
    fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); act(() => node.focus()); await h.reply(2, { type: 'pipelines', pipelines: [{ ...pipeline, edges: [{ from: 'implement', to: 'review', when: 'Updated after reconnect' }] }] });
    expect(screen.getByLabelText('Pipeline transitions')).toHaveTextContent('Updated after reconnect'); expect(screen.getByRole('group', { name: 'Pipeline graph' })).toBe(graph); expect(node).toHaveFocus(); expect(graph).toHaveAttribute('viewBox', viewBox); expect(node).toHaveAttribute('aria-pressed', 'true');
  });
  it('aborts obsolete group reads, hides prior-group data/errors and ignores late results', async () => {
    const h = harness(); await h.reply(0); fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle();
    h.view.rerender(<Provider store={h.store}><PipelineExplorer group="Other" onEdit={h.edit} /></Provider>); await settle();
    expect(h.pending[1]!.signal.aborted).toBe(true); expect(screen.queryByRole('group', { name: 'Pipeline graph' })).not.toBeInTheDocument();
    await h.reply(2, { type: 'pipelines', pipelines: [{ ...pipeline, name: 'Other pipeline' }] });
    await h.reply(1, { type: 'pipelines', pipelines: [{ ...pipeline, name: 'Obsolete pipeline' }] });
    expect(screen.getByLabelText('Pipeline')).toHaveValue('Other pipeline'); expect(screen.queryByText('Obsolete pipeline')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle();
    h.view.unmount(); act(() => { h.store.dispatch(connectionActions.connected({ at: 4, reconnect: true })); h.snapshot(); }); await settle(); expect(h.pending).toHaveLength(4); expect(h.pending[3]!.signal.aborted).toBe(true);
  });
  it('waits for synchronization and preserves accepted content through disconnect and in-place resync', async () => {
    const h = harness(); await h.reply(0); const graph = screen.getByRole('group', { name: 'Pipeline graph' });
    fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle();
    act(() => { h.store.dispatch(connectionActions.resyncRequested({ at: 2, reason: 'Sequence gap' })); }); await settle();
    expect(h.pending[1]!.signal.aborted).toBe(true); expect(h.pending).toHaveLength(2); expect(graph).toBeVisible(); expect(screen.getByRole('button', { name: 'Discover pipelines' })).toBeDisabled();
    act(() => h.snapshot()); await settle(); expect(h.pending).toHaveLength(3); await h.reply(2);
    act(() => { h.store.dispatch(connectionActions.disconnected({ at: 3 })); }); await settle(); expect(graph).toBeVisible();
    act(() => { h.store.dispatch(connectionActions.connected({ at: 4, reconnect: true })); }); await settle(); expect(h.pending).toHaveLength(3);
    act(() => h.snapshot()); await settle(); expect(h.pending).toHaveLength(4); expect(screen.getByRole('group', { name: 'Pipeline graph' })).toBe(graph);
    h.view.unmount(); expect(h.pending[3]!.signal.aborted).toBe(true);
  });
  it('retains accepted data for malformed or foreign-scope payloads and clears removed selections', async () => {
    const h = harness(); const other = { ...pipeline, name: 'Release' }; await h.reply(0, { type: 'pipelines', pipelines: [pipeline, other] });
    fireEvent.change(screen.getByLabelText('Pipeline'), { target: { value: 'Release' } }); const graph = screen.getByRole('group', { name: 'Pipeline graph' });
    for (const frame of [{ type: 'pipelines', group: 'Wrong', pipelines: [] }, { type: 'pipelines', pipelines: [{ name: 'Broken', actions: null }] }, { type: 'pipelines', pipelines: [{ ...pipeline, edges: [null] }] }]) {
      fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle(); await h.reply(h.pending.length - 1, frame);
      expect(screen.getByRole('alert')).toHaveTextContent('Unexpected pipeline discovery response'); expect(screen.getByRole('group', { name: 'Pipeline graph' })).toBe(graph); expect(screen.getByLabelText('Pipeline')).toHaveValue('Release');
    }
    fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle(); await h.reply(h.pending.length - 1);
    expect(screen.getByLabelText('Pipeline')).toHaveValue('Build'); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle(); await h.reply(h.pending.length - 1, { type: 'pipelines', pipelines: [other, pipeline] });
    expect(screen.getByLabelText('Pipeline')).toHaveValue('Build');
    fireEvent.click(screen.getByRole('button', { name: 'Discover pipelines' })); await settle(); await h.reply(h.pending.length - 1, { type: 'pipelines', pipelines: [] });
    expect(screen.queryByRole('group', { name: 'Pipeline graph' })).not.toBeInTheDocument(); expect(screen.getByText('No connected action pipelines in this group.')).toBeVisible();
  });

});
