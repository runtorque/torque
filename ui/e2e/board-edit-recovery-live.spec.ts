import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function fixture(request: APIRequestContext, title: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `${title} ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } });
  const id = String((await command(request, { cmd: 'board_add_task', group, task: title, lane: 'Backlog', description: 'Saved description' })).task_id);
  const upload = await (await request.post('/api/upload', { multipart: { task_id: id, file: { name: 'original.png', mimeType: 'image/png', buffer: Buffer.from('original image') } } })).json() as { ok: boolean; data: Row[] };
  expect(upload.ok).toBe(true); await command(request, { cmd: 'board_update_task', id, attachments: upload.data }); return { id, group };
}
test('Board edits recover from real save and cleanup deadlines without clearing drafts or repeating acknowledged edits', async ({ page, request }) => {
  test.setTimeout(120_000); const title = 'Recover task edits'; const { id } = await fixture(request, title);
  let saveMode = 'hold'; let holdCleanup = true; let savedHeld = false; let cleanupHeld = false; let releaseSave = () => {}; let releaseCleanup = () => {}; const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'board_update_task' || data.cmd === 'remove_attachment') writes.push(data);
    if (data.cmd === 'board_update_task' && saveMode === 'hold') { savedHeld = true; saveMode = 'invalid'; await new Promise<void>((resolve) => { releaseSave = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'state', seq: 1, board_tasks: {} } } }); return; }
    if (data.cmd === 'board_update_task' && saveMode === 'invalid') { saveMode = 'valid'; await route.fulfill({ json: { ok: true, data: { type: 'state', seq: 1, board_tasks: { other: { id: 'other' } } } } }); return; }
    if (data.cmd === 'remove_attachment' && holdCleanup) { holdCleanup = false; const response = await route.fetch(); cleanupHeld = true; await new Promise<void>((resolve) => { releaseCleanup = resolve; }); await route.fulfill({ response }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByText(title, { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: title, exact: true }); const draft = dialog.getByRole('textbox', { name: 'Description', exact: true });
    await draft.fill('Retained edit intent'); await draft.evaluate((node: HTMLTextAreaElement) => { node.dataset.owner = 'retained'; });
    await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click(); await dialog.getByRole('button', { name: 'Remove attachment original.png', exact: true }).click();
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect.poll(() => savedHeld).toBe(true); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 });
    await expect(draft).toHaveValue('Retained edit intent'); await expect(draft).toHaveAttribute('data-owner', 'retained'); await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeEnabled(); expect(writes.map((item) => item.cmd)).toEqual(['board_update_task']); releaseSave();
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('invalid acknowledgement'); await expect(draft).toHaveValue('Retained edit intent'); expect(writes).toHaveLength(2);
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect.poll(() => cleanupHeld).toBe(true); await expect(dialog.getByRole('alert')).toContainText('Task changes saved, but attachment cleanup failed', { timeout: 35_000 });
    await page.setViewportSize({ width: 760, height: 600 }); await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeInViewport(); await page.screenshot({ path: test.info().outputPath('board-edit-partial-cleanup.png') });
    await command(request, { cmd: 'board_update_task', id, description: 'Later external edit' }); const before = connections; await socket!.close({ code: 1012, reason: 'Do not replay task mutations' }); await expect.poll(() => connections).toBeGreaterThan(before); expect(writes).toHaveLength(4); releaseCleanup();
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect(dialog).toHaveCount(0); expect(writes.map((item) => item.cmd)).toEqual(['board_update_task', 'board_update_task', 'board_update_task', 'remove_attachment', 'remove_attachment']);
    const saved = (await command(request, { cmd: 'task_detail', id })).task as Row; expect(saved.description).toBe('Later external edit'); expect(saved.attachments).toEqual([]); expect((await request.get(`/attachments/${encodeURIComponent(id)}/original.png`)).status()).toBe(404);
  } finally { releaseSave(); releaseCleanup(); }
});
test('Board uploads reject unrelated metadata and retain accepted evidence through a real later upload timeout', async ({ page, request }) => {
  test.setTimeout(75_000); const title = 'Recover task uploads'; const { id } = await fixture(request, title);
  let invalid = true; let hold = false; let held = false; let release = () => {};
  await page.route('**/api/upload', async (route) => {
    if (invalid) { invalid = false; await route.fulfill({ json: { ok: true, data: [{ filename: 'wrong.png', path: '/attachments/other/wrong.png', mime_type: 'image/png' }] } }); return; }
    if (hold) { hold = false; held = true; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: true, data: [{ filename: 'late.png', path: `/attachments/${id}/late.png`, mime_type: 'image/png' }] } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByText(title, { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: title, exact: true }); await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
    const upload = dialog.getByLabel('Upload evidence files', { exact: true });
    await upload.setInputFiles({ name: 'wrong.png', mimeType: 'image/png', buffer: Buffer.from('bad response') }); await expect(dialog.getByRole('alert')).toContainText('invalid upload acknowledgement'); await expect(dialog.getByRole('button', { name: 'Remove attachment wrong.png', exact: true })).toHaveCount(0);
    await upload.setInputFiles({ name: 'accepted.png', mimeType: 'image/png', buffer: Buffer.from('accepted upload') }); await expect(dialog.getByRole('button', { name: 'Remove attachment accepted.png', exact: true })).toBeVisible();
    hold = true; await upload.setInputFiles({ name: 'late.png', mimeType: 'image/png', buffer: Buffer.from('unacknowledged upload') }); await expect.poll(() => held).toBe(true); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 });
    await expect(dialog.getByRole('button', { name: 'Remove attachment accepted.png', exact: true })).toBeVisible(); await expect(dialog.getByRole('button', { name: 'Remove attachment original.png', exact: true })).toBeVisible(); release(); await expect(dialog.getByRole('button', { name: 'Remove attachment late.png', exact: true })).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('board-upload-timeout.png') }); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const saved = (await command(request, { cmd: 'task_detail', id })).task as Row; expect((saved.attachments as Row[]).map((item) => item.filename)).toEqual(['original.png']); expect((await request.get(`/attachments/${encodeURIComponent(id)}/accepted.png`)).status()).toBe(404); expect((await request.get(`/attachments/${encodeURIComponent(id)}/original.png`)).status()).toBe(200);
  } finally { release(); }
});
