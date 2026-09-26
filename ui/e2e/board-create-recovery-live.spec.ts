import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
async function fixture(request: APIRequestContext) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Creation recovery ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } }); return group;
}
test('unknown creation recovers the original receipt and staged evidence without duplicate tasks', async ({ page, request }) => {
  test.setTimeout(60_000); const group = await fixture(request); const writes: Row[] = []; let held = false; let release = () => {}; let id = '';
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'board_add_task') { await route.continue(); return; }
    writes.push(data);
    if (writes.length === 1) { const result = await route.fetch(); id = String(((await result.json()) as { data: Row }).data.task_id); held = true; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response: result }); return; }
    if (writes.length === 2) { await route.fulfill({ json: { ok: true, data: { type: 'board_task_added', task_id: id, title: 'Unrelated task' } } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: '＋ New task', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Create task', exact: true });
    await dialog.getByLabel('Title', { exact: true }).fill('Recover reviewed creation'); await dialog.getByText('Attachments and artifacts · 0', { exact: true }).click();
    await dialog.getByLabel('Upload evidence files').setInputFiles({ name: 'review.png', mimeType: 'image/png', buffer: Buffer.from('reviewed evidence') }); await expect(dialog.getByRole('button', { name: 'Remove attachment review.png', exact: true })).toBeEnabled();
    await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect.poll(() => held).toBe(true); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 });
    await expect(dialog.getByRole('button', { name: 'Retry creation', exact: true })).toBeInViewport(); await expect(dialog.getByLabel('Title', { exact: true })).toHaveValue('Recover reviewed creation'); await expect(dialog.getByLabel('Title', { exact: true })).toBeDisabled(); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('Retry creation');
    await page.setViewportSize({ width: 760, height: 600 }); await expect(dialog.getByRole('button', { name: 'Retry creation', exact: true })).toBeInViewport(); await page.screenshot({ path: test.info().outputPath('board-create-unknown.png') }); release();
    await dialog.getByRole('button', { name: 'Retry creation', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('invalid creation acknowledgement');
    await dialog.getByRole('button', { name: 'Retry creation', exact: true }).click(); await expect(dialog).toHaveCount(0); expect(writes).toHaveLength(3); expect(writes[1]).toEqual(writes[0]); expect(writes[2]).toEqual(writes[0]); expect(writes[0]!.idempotency_key).not.toBe(writes[0]!.id);
    const state = await command(request, { cmd: 'get_state' }); expect(Object.values(state.board_tasks as Record<string, Row>).filter((row) => row.group === group)).toHaveLength(1);
    const task = (await command(request, { cmd: 'task_detail', id })).task as Row; expect((task.attachments as Row[])[0]!.path).toContain(`/${id}/review.png`); expect((await request.get(`/attachments/${encodeURIComponent(id)}/review.png`)).status()).toBe(200);
  } finally { release(); }
});
test('draft uploads and cleanup recover real deadlines while retaining accepted evidence', async ({ page, request }) => {
  test.setTimeout(90_000); await fixture(request); let uploads = 0; let releaseUpload = () => {}; let releaseCleanup = () => {}; let cleanups = 0; let token = '';
  await page.route('**/api/upload', async (route) => {
    uploads++; if (uploads === 1) { const response = await route.fetch(); const data = (await response.json()) as { data: Row[] }; token = String(data.data[0]!.path).split('/').at(-2)!; await route.fulfill({ response }); return; }
    await new Promise<void>((resolve) => { releaseUpload = resolve; }); await route.fulfill({ json: { ok: true, data: [{ filename: 'late.png', path: `/attachments/${token}/late.png`, mime_type: 'image/png' }] } });
  });
  await page.route('**/api/upload/cleanup', async (route) => { cleanups++; if (cleanups === 1) { const response = await route.fetch(); await new Promise<void>((resolve) => { releaseCleanup = resolve; }); await route.fulfill({ response }); } else await route.continue(); });
  try {
    await page.goto('/'); await page.getByRole('button', { name: '＋ New task', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Create task', exact: true });
    await dialog.getByLabel('Title', { exact: true }).fill('Retain draft uploads'); await dialog.getByText('Attachments and artifacts · 0', { exact: true }).click();
    await dialog.getByLabel('Upload evidence files').setInputFiles({ name: 'accepted.png', mimeType: 'image/png', buffer: Buffer.from('accepted') }); await expect(dialog.getByRole('button', { name: 'Remove attachment accepted.png', exact: true })).toBeVisible();
    await dialog.getByLabel('Upload evidence files').setInputFiles({ name: 'late.png', mimeType: 'image/png', buffer: Buffer.from('late') }); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); releaseUpload();
    await expect(dialog.getByRole('button', { name: 'Remove attachment late.png', exact: true })).toHaveCount(0); await expect(dialog.getByRole('button', { name: 'Remove attachment accepted.png', exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(dialog.getByLabel('Title', { exact: true })).toHaveValue('Retain draft uploads'); await page.setViewportSize({ width: 760, height: 600 }); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeInViewport(); await expect(dialog.getByRole('button', { name: 'Create task', exact: true })).toBeInViewport(); await expect(dialog.getByRole('button', { name: 'Create task', exact: true })).toBeDisabled(); await expect(dialog.getByLabel('Title', { exact: true })).toBeDisabled(); await page.screenshot({ path: test.info().outputPath('board-draft-cleanup-unknown.png') }); releaseCleanup();
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(dialog).toHaveCount(0); expect(cleanups).toBe(2); expect((await request.get(`/attachments/${encodeURIComponent(token)}/accepted.png`)).status()).toBe(404);
  } finally { releaseUpload(); releaseCleanup(); }
});

test('Unconfirmed draft file removal blocks creation until explicit retry', async ({ page, request }) => {
  test.setTimeout(60_000); await fixture(request); let release = () => {}; let removals = 0; let draftId = '';
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'remove_attachment' && ++removals === 1) {
      draftId = String(data.task_id); const response = await route.fetch();
      await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response }); return;
    }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: '＋ New task', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create task', exact: true }); await dialog.getByLabel('Title', { exact: true }).fill('Confirm evidence removal');
    await dialog.getByText('Attachments and artifacts · 0', { exact: true }).click();
    await dialog.getByLabel('Upload evidence files').setInputFiles({ name: 'removed.png', mimeType: 'image/png', buffer: Buffer.from('remove evidence') });
    await dialog.getByRole('button', { name: 'Remove attachment removed.png', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 });
    await expect(dialog.getByRole('button', { name: 'Create task', exact: true })).toBeDisabled(); await expect(dialog.getByLabel('Title', { exact: true })).toBeDisabled();
    expect((await request.get(`/attachments/${encodeURIComponent(draftId)}/removed.png`)).status()).toBe(404);
    await page.setViewportSize({ width: 760, height: 600 }); await dialog.getByRole('button', { name: 'Retry removal', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath('board-create-removal-unknown.png') }); release();
    await dialog.getByRole('button', { name: 'Retry removal', exact: true }).click(); await expect(dialog.getByRole('button', { name: 'Remove attachment removed.png', exact: true })).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Create task', exact: true })).toBeEnabled(); await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const state = await command(request, { cmd: 'get_state' }); const created = Object.values(state.board_tasks as Record<string, Row>).find((row) => row.task === 'Confirm evidence removal')!;
    const saved = (await command(request, { cmd: 'task_detail', id: created.id })).task as Row; expect(saved.attachments).toEqual([]); expect(removals).toBe(2);
  } finally { release(); }
});
