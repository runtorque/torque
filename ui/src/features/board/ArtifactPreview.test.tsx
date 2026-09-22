import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ArtifactPreview } from './ArtifactPreview';
import { evidenceUrl, uploadedEvidence } from './taskEvidenceModel';
afterEach(() => vi.unstubAllGlobals());
it('renders inline draft text safely without fetching the persisted file', () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  render(<ArtifactPreview taskId="task" item={{ type: 'diff', title: 'Draft', filename: 'report.diff', content: '<script>unsaved</script>' }} onClose={vi.fn()} />);
  expect(screen.getByText('<script>unsaved</script>')).toBeVisible(); expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole('link', { name: 'Download file' })).toHaveAttribute('href', '/attachments/task/report.diff');
});
it('loads file text only when opened and aborts on close', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn(() => new Promise((resolve) => { finish = resolve; })); vi.stubGlobal('fetch', fetcher);
  const { unmount } = render(<ArtifactPreview taskId="task" item={{ type: 'log', filename: 'worker.log' }} onClose={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading file preview');
  const signal = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].signal!;
  await act(async () => { finish({ ok: true, text: () => Promise.resolve('File evidence') }); await Promise.resolve(); });
  expect(screen.getByText('File evidence')).toBeVisible(); unmount(); expect(signal.aborted).toBe(true);
});
it('reports file failures and does not invent links for path references', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false })));
  const view = render(<ArtifactPreview taskId="task" item={{ type: 'log', filename: 'missing.log' }} onClose={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this file'); view.unmount();
  render(<ArtifactPreview taskId="task" item={{ type: 'file_ref', path: '/project/source.ts', line_start: 2, line_end: 4 }} onClose={vi.fn()} />);
  expect(screen.getByText('Lines 2–4')).toBeVisible(); expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(evidenceUrl('task', { url: 'javascript:bad()' })).toBe('');
  expect(evidenceUrl('task', { type: 'file_ref', filename: 'source.ts', path: '/project/source.ts', lifecycle: { owner: 'external' } })).toBe('');
});
it('previews images using their source task and classifies text uploads as artifacts', async () => {
  render(<ArtifactPreview taskId="task" item={{ mime_type: 'image/png', filename: 'image.png', taskId: 'source' }} onClose={vi.fn()} />);
  expect(screen.getByRole('img')).toHaveAttribute('src', '/attachments/source/image.png');
  const file = new File(['log content'], 'worker.log', { type: 'text/plain' });
  Object.defineProperty(file, 'text', { value: () => Promise.resolve('log content') });
  expect(await uploadedEvidence({ filename: 'worker.log', path: '/files/worker.log', mime_type: 'text/plain' }, file)).toMatchObject({ kind: 'artifact', item: { type: 'log', content: 'log content', lifecycle: { owner: 'task' } } });
});
