import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Provider } from 'react-redux';
import { connectionActions, createAppStore } from '../../app/store';
import { ArtifactPreview } from './ArtifactPreview';
import { evidencePreviewKind, evidenceUrl, uploadedEvidence } from './taskEvidenceModel';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
function mount(item: Record<string, unknown>) {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const show = (value: Record<string, unknown>) => <Provider store={store}><ArtifactPreview taskId="task" item={value} onClose={vi.fn()} /></Provider>;
  const view = render(show(item)); return { ...view, store, replace: (value: Record<string, unknown>) => view.rerender(show(value)) };
}
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
function held<T>() { let resolve!: (value: T) => void; return { promise: new Promise<T>((done) => { resolve = done; }), resolve: (value: T) => resolve(value) }; }
it('renders inline draft text safely without fetching the persisted file', () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  mount({ type: 'diff', title: 'Draft', filename: 'report.diff', content: '<script>unsaved</script>' });
  expect(screen.getByText('<script>unsaved</script>')).toBeVisible(); expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole('link', { name: 'Download file' })).toHaveAttribute('href', '/attachments/task/report.diff');
});
it('loads file text only when opened', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn(() => new Promise((resolve) => { finish = resolve; })); vi.stubGlobal('fetch', fetcher);
  const { unmount } = mount({ type: 'log', filename: 'worker.log' });
  expect(screen.getByRole('status')).toHaveTextContent('Loading file preview');
  await act(async () => { finish({ ok: true, text: () => Promise.resolve('File evidence') }); await Promise.resolve(); });
  expect(screen.getByText('File evidence')).toBeVisible(); unmount();
});
it('reports file failures and does not invent links for path references', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false })));
  const view = mount({ type: 'log', filename: 'missing.log' });
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this file'); view.unmount();
  mount({ type: 'file_ref', path: '/project/source.ts', line_start: 2, line_end: 4 });
  expect(screen.getByText('Lines 2–4')).toBeVisible(); expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(evidenceUrl('task', { url: 'javascript:bad()' })).toBe('');
  expect(evidenceUrl('task', { type: 'file_ref', filename: 'source.ts', path: '/project/source.ts', lifecycle: { owner: 'external' } })).toBe('');
});
it('previews images using their source task and classifies text uploads as artifacts', async () => {
  mount({ mime_type: 'image/png', filename: 'image.png', taskId: 'source' });
  expect(screen.getByRole('img')).toHaveAttribute('src', '/attachments/source/image.png');
  const file = new File(['log content'], 'worker.log', { type: 'text/plain' });
  Object.defineProperty(file, 'text', { value: () => Promise.resolve('log content') });
  expect(await uploadedEvidence({ filename: 'worker.log', path: '/files/worker.log', mime_type: 'text/plain' }, file)).toMatchObject({ kind: 'artifact', item: { type: 'log', content: 'log content', lifecycle: { owner: 'task' } } });
});
it.each(['picture.PNG', 'picture.jpg', 'picture.jpeg', 'picture.gif', 'picture.webp', 'picture.svg'])('recognizes image extension %s without MIME metadata', (filename) => {
  expect(evidencePreviewKind({ filename })).toBe('image');
});
it.each(['md', 'markdown', 'txt', 'diff', 'patch', 'json', 'log', 'csv', 'yaml', 'yml', 'xml', 'htm', 'html', 'js', 'css'])('recognizes Classic text extension %s without MIME metadata', (extension) => {
  expect(evidencePreviewKind({ filename: `report.${extension.toUpperCase()}` })).toBe('text');
});
it('normalizes MIME/type casing and recognizes nested inline storage', () => {
  expect(evidencePreviewKind({ mime_type: ' IMAGE/PNG; charset=UTF-8 ' })).toBe('image');
  expect(evidencePreviewKind({ mime_type: ' TEXT/PLAIN; charset=UTF-8 ' })).toBe('text');
  expect(evidencePreviewKind({ type: 'DIFF' })).toBe('text');
  expect(evidencePreviewKind({ storage: { kind: 'inline', content: 'Nested draft' } })).toBe('text');
});
it('bounds file body loading, retries without closing and ignores the expired response', async () => {
  vi.useFakeTimers(); const pending = held<string>(); const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, text: () => pending.promise }).mockResolvedValue({ ok: true, text: () => Promise.resolve('Recovered file') }); vi.stubGlobal('fetch', fetcher);
  mount({ type: 'log', filename: 'worker.log' }); await flush(); const signal = (fetcher.mock.calls[0]![1] as RequestInit).signal as AbortSignal;
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(signal.aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.queryByRole('status')).not.toBeInTheDocument();
  const retry = screen.getByRole('button', { name: 'Retry file preview' }); retry.focus(); fireEvent.click(retry); await flush(); expect(screen.getByText('Recovered file')).toBeVisible(); expect(screen.getByRole('button', { name: 'Refresh file preview' })).toBe(retry); expect(retry).toHaveFocus();
  pending.resolve('Expired file'); await flush(); expect(screen.queryByText('Expired file')).not.toBeInTheDocument(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('refreshes a visible file on reconnect while retaining the accepted preview and reading node', async () => {
  vi.useFakeTimers(); const pending = held<string>(); const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, text: () => Promise.resolve('Accepted content') }).mockResolvedValueOnce({ ok: true, text: () => pending.promise }).mockResolvedValue({ ok: true, text: () => Promise.resolve('Refreshed content') }); vi.stubGlobal('fetch', fetcher);
  const { store, unmount } = mount({ type: 'log', filename: 'worker.log' }); await flush(); const content = screen.getByText('Accepted content'); const download = screen.getByRole('link', { name: 'Download file' }); download.focus();
  act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await flush(); expect(screen.getByText('Accepted content')).toBe(content);
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByText('Accepted content')).toBe(content); expect(download).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Retry file preview' })); await flush(); expect(screen.getByText('Refreshed content')).toBe(content);
  unmount(); const count = fetcher.mock.calls.length; act(() => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); }); expect(fetcher).toHaveBeenCalledTimes(count);
});
it('clears another file content immediately on target replacement and ignores its late body', async () => {
  const pending = held<string>(); const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, text: () => pending.promise }).mockResolvedValue({ ok: true, text: () => Promise.resolve('Current file') }); vi.stubGlobal('fetch', fetcher);
  const { replace } = mount({ type: 'log', filename: 'old.log' }); await flush(); const signal = (fetcher.mock.calls[0]![1] as RequestInit).signal as AbortSignal;
  replace({ type: 'log', filename: 'current.log' }); await flush(); expect(signal.aborted).toBe(true); expect(screen.getByText('Current file')).toBeVisible();
  pending.resolve('Old file'); await flush(); expect(screen.queryByText('Old file')).not.toBeInTheDocument();
  replace({ type: 'snippet', content: 'New inline draft' }); await flush(); expect(screen.queryByText('Current file')).not.toBeInTheDocument(); expect(screen.getByText('New inline draft')).toBeVisible(); expect(fetcher).toHaveBeenCalledTimes(2);
});
it('offers an image retry while keeping the source-task download target', () => {
  mount({ filename: 'picture.PNG', taskId: 'source' }); const image = screen.getByRole('img'); fireEvent.error(image);
  expect(screen.getByRole('alert')).toHaveTextContent('Could not load this image'); const retryButton = screen.getByRole('button', { name: 'Retry image preview' }); retryButton.focus(); fireEvent.click(retryButton);
  const retry = screen.getByRole('img'); expect(retry).not.toBe(image); expect(retry).toHaveAttribute('src', '/attachments/source/picture.PNG'); fireEvent.load(retry); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Refresh image preview' })).toBe(retryButton); expect(retryButton).toHaveFocus();
});

it('aborts an unfinished body on close and leaves a replacement preview untouched', async () => {
  const pending = held<string>(); const fetcher = vi.fn().mockResolvedValue({ ok: true, text: () => pending.promise }); vi.stubGlobal('fetch', fetcher);
  const first = mount({ filename: 'old.log' }); await flush(); const signal = (fetcher.mock.calls[0]![1] as RequestInit).signal as AbortSignal; first.unmount(); expect(signal.aborted).toBe(true);
  mount({ type: 'snippet', content: 'Replacement preview' }); pending.resolve('Closed preview'); await flush(); expect(screen.getByText('Replacement preview')).toBeVisible(); expect(screen.queryByText('Closed preview')).not.toBeInTheDocument();
});
it('bounds an image load and ignores its late completion until explicitly retried', async () => {
  vi.useFakeTimers(); mount({ filename: 'slow.png' }); const initial = screen.getByRole('img');
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.queryByRole('status')).not.toBeInTheDocument();
  fireEvent.load(initial); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); fireEvent.click(screen.getByRole('button', { name: 'Retry image preview' })); fireEvent.load(screen.getByRole('img')); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('renders empty files without substituting their metadata and keeps inline storage local', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve('') }); vi.stubGlobal('fetch', fetcher);
  const view = mount({ filename: 'empty.log', summary: 'File metadata' }); await flush(); expect(screen.getByText('This file is empty.')).toBeVisible();
  view.replace({ filename: 'draft.txt', storage: { kind: 'inline', content: 'Nested draft' } }); expect(screen.getByText('Nested draft')).toBeVisible(); expect(screen.queryByRole('link')).not.toBeInTheDocument(); expect(fetcher).toHaveBeenCalledTimes(1);
  expect(evidenceUrl('task', { storage: { kind: 'path', path: '/attachments/task/worker.log' } })).toBe('/attachments/task/worker.log');
  expect(evidenceUrl('task', { filename: 'source.txt', storage: { kind: 'file_ref', path: '/repo/source.txt' } })).toBe('');
});

it('resolves uploaded absolute paths through the attachment endpoint without exposing filesystem URLs', () => {
  const absolute = '/private/tmp/qa/attachments/task/image.png';
  expect(evidenceUrl('task', { filename: 'image.png', path: absolute, mime_type: 'image/png' })).toBe('/attachments/task/image.png');
  expect(evidenceUrl('task', { type: 'file_ref', filename: 'image.png', storage: { kind: 'path', path: absolute } })).toBe('/attachments/task/image.png');
  expect(evidenceUrl('task', { type: 'file_ref', filename: 'external.txt', path: '/repo/external.txt' })).toBe('');
});

it('preserves explicit safe download URLs without inferring uploads for external or inline storage', () => {
  expect(evidenceUrl('task', { filename: 'reference.txt', storage: { kind: 'file_ref', path: '/repo/reference.txt' }, url: 'https://example.test/reference.txt' })).toBe('https://example.test/reference.txt');
  expect(evidenceUrl('task', { filename: 'reference.txt', storage: { kind: 'file_ref' } })).toBe('');
  expect(evidenceUrl('task', { filename: 'draft.txt', storage: { kind: 'inline', content: 'Draft' }, url: 'https://example.test/draft.txt' })).toBe('https://example.test/draft.txt');
  expect(evidenceUrl('task', { storage: { kind: 'inline' }, url: 'javascript:bad()' })).toBe('');
});
