import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
test('external comments retain drafts through refusal, mismatched acknowledgement and deadline; unlink persists only after acknowledgement', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `External actions ${Date.now()}`; let id = ''; const writes: Row[] = []; let posts = 0; let unlinks = 0; let release: (() => void) | undefined; let socket: WebSocketRoute | undefined; let connections = 0;
  // Every hosted comment is intercepted. Unlink is a local SQLite operation on
  // a disposable group whose provider sync is disabled.
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'external_post_task_comment') { writes.push(data); posts++;
      if (posts === 1) { await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Local comment refusal' } } }); return; }
      if (posts === 2) { await route.fulfill({ json: { ok: true, data: { type: 'external_comment_posted', task_id: 'wrong-task' } } }); return; }
      if (posts === 3) await new Promise<void>((resolve) => { release = resolve; });
      await route.fulfill({ json: { ok: true, data: { type: 'external_comment_posted', task_id: id } } }).catch(() => {}); return;
    }
    if (data.cmd === 'external_link_task') { writes.push(data); unlinks++; if (unlinks === 1) { await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Local unlink refusal' } } }); return; } }
    await route.continue();
  });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; const server = client.connectToServer(); client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (data.cmd === 'external_post_task_comment') { throw new Error('Comment must use the intercepted owned HTTP path'); } server.send(raw); }); });
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { board_sync_enabled: false, board_sync_provider: '' } });
    id = String((await command(request, { cmd: 'board_add_task', group, task: 'Reviewed external operations', lane: 'Backlog', provider: 'qa-local', external_id: 'LOCAL-1', external_url: 'https://example.invalid/LOCAL-1', board_sync: { version: 1, enabled: false } })).task_id);
    await page.goto('/'); await page.getByRole('button', { name: /▦ Board/ }).click(); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByText('Reviewed external operations', { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: 'Reviewed external operations', exact: true });
    const description = dialog.getByRole('textbox', { name: 'Description', exact: true }); await description.fill('Unrelated retained draft'); await dialog.getByRole('tab', { name: 'Integrations', exact: true }).click(); const comment = dialog.getByRole('textbox', { name: 'Post comment', exact: true }); await comment.fill(' Reviewed comment ');
    const post = dialog.getByRole('button', { name: 'Post', exact: true }); await post.click(); await expect(dialog.getByRole('alert')).toContainText('Local comment refusal'); await expect(comment).toHaveValue(' Reviewed comment ');
    const before = connections; await socket!.close({ code: 1012, reason: 'External draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(comment).toHaveValue(' Reviewed comment ');
    await post.click(); await expect(dialog.getByRole('alert')).toContainText('acknowledgement did not match'); await expect(comment).toHaveValue(' Reviewed comment ');
    await post.click(); await expect(post).toBeDisabled(); await expect(comment).toHaveValue(' Reviewed comment '); await page.keyboard.press('Escape'); await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); expect(posts).toBe(3); await expect(post).toBeEnabled(); await comment.fill('A new reviewed comment'); release!(); await expect(comment).toHaveValue('A new reviewed comment');
    await post.click(); await expect(comment).toHaveValue(''); expect(posts).toBe(4); expect(writes.filter((row) => row.cmd === 'external_post_task_comment').map((row) => row.comment)).toEqual(['Reviewed comment', 'Reviewed comment', 'Reviewed comment', 'A new reviewed comment']);
    const unlink = dialog.getByRole('button', { name: 'Unlink', exact: true }); await unlink.click(); await expect(dialog.getByRole('alert')).toContainText('Local unlink refusal'); await expect(dialog.getByRole('textbox', { name: 'External ID', exact: true })).toHaveValue('LOCAL-1');
    await unlink.click(); await expect(dialog.getByRole('textbox', { name: 'External ID', exact: true })).toHaveValue(''); const unlinked = (await command(request, { cmd: 'task_detail', id })).task as Row; expect(unlinked.external_id).toBe(''); expect(unlinked.messages).toEqual([]);
    await command(request, { cmd: 'board_update_task', id, provider: 'qa-local', external_id: 'LOCAL-2', external_url: 'https://example.invalid/LOCAL-2' }); await expect(description).toHaveValue('Unrelated retained draft'); await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const saved = (await command(request, { cmd: 'task_detail', id })).task as Row; expect(saved.external_id).toBe('LOCAL-2'); expect(saved.description).toBe('Unrelated retained draft'); expect(saved.messages).toEqual([]);
    await page.getByText('Reviewed external operations', { exact: true }).dblclick(); await dialog.getByRole('tab', { name: 'Integrations', exact: true }).click(); await expect(dialog.getByRole('textbox', { name: 'External ID', exact: true })).toHaveValue('LOCAL-2'); await page.screenshot({ path: test.info().outputPath('external-actions.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('external-actions.json'), JSON.stringify({ writes, saved }, null, 2));
  } finally { release?.(); if (id) await command(request, { cmd: 'board_remove_task', id }); await command(request, { cmd: 'remove_group', group }); }
});
