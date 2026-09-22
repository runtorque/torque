import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { TaskPromptPreview } from './TaskPromptPreview';
import { resolveActionVariables } from './actionVariables';
afterEach(() => vi.unstubAllGlobals());
it('does not present a late response as the preview of a newer draft', async () => {
  let complete!: (value: unknown) => void;
  const fetcher = vi.fn(() => new Promise((resolve) => { complete = resolve; })); vi.stubGlobal('fetch', fetcher);
  const { rerender, unmount } = render(<TaskPromptPreview inputsKey="old" command={() => ({ cmd: 'preview_prompt', task: 'old' })} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' }));
  expect(screen.getByRole('button', { name: 'Rendering prompt…' })).toBeDisabled();
  rerender(<TaskPromptPreview inputsKey="new" command={() => ({ cmd: 'preview_prompt', task: 'new' })} />);
  await act(async () => { complete({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'prompt_preview', prompt: 'Old response' } }) }); await Promise.resolve(); });
  expect(screen.queryByText('Old response')).not.toBeInTheDocument();
  expect(screen.getByText('Draft changed. Preview again to see the current prompt.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' }));
  const request = (fetcher.mock.calls as unknown as [string, RequestInit][]).at(-1)!;
  unmount(); expect(request[1].signal?.aborted).toBe(true);
});
it('preserves structured and unknown values along with explicit empty, zero and false overrides', () => {
  expect(resolveActionVariables('{"SCOPE":"","COUNT":0,"FLAG":false,"extra":{"nested":true}}', [{ name: 'SCOPE', default: 'all' }, { name: 'COUNT', default: 3 }, { name: 'FLAG', default: true }, { name: 'missing', default: '' }])).toEqual({ SCOPE: '', COUNT: 0, FLAG: false, extra: { nested: true } });
  expect(() => resolveActionVariables('[]', [])).toThrow('JSON object');
});
