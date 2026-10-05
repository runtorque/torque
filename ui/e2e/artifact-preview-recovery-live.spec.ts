import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('artifact previews recover deadlines, reconnect and image failure without losing the enclosing task draft', async ({ page, request }) => {
  test.setTimeout(85_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Artifact QA ${Date.now()}`; await command(request, { cmd: 'add_group', group });
  const id = String((await command(request, { cmd: 'board_add_task', group, task: 'Recover artifact previews', description: 'Saved task scope' })).task_id);
  const files = [{ name: 'legacy.LOG', mimeType: 'text/plain', buffer: Buffer.from('Actual persisted evidence\nsecond line') }, { name: 'legacy.PNG', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGzQAAAAASUVORK5CYII=', 'base64') }];
  const artifacts: Row[] = [];
  for (const file of files) {
    const uploaded = await (await request.post('/api/upload', { multipart: { task_id: id, file } })).json() as { ok: boolean; data: Row[] }; expect(uploaded.ok).toBe(true);
    artifacts.push({ ...uploaded.data[0], id: file.name, title: file.name, type: 'file_ref', mime_type: '', content: '', storage: { kind: 'path', path: uploaded.data[0]!.path }, lifecycle: { owner: 'task' } });
  }
  await command(request, { cmd: 'board_update_task', id, artifacts }); await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } });
  let socket: WebSocketRoute | undefined; let connections = 0; let hold = true; let held = 0; let release = () => {}; let textReads = 0; let failImage = true; let imageReads = 0;
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route(`**/attachments/${encodeURIComponent(id)}/legacy.LOG`, async (route) => { textReads++; if (hold) { hold = false; held++; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ status: 200, contentType: 'text/plain', body: 'Obsolete delayed body' }); return; } await route.continue(); });
  await page.route(`**/attachments/${encodeURIComponent(id)}/legacy.PNG`, async (route) => { imageReads++; if (failImage) { failImage = false; await route.fulfill({ status: 503, body: 'Injected image failure' }); return; } await route.continue(); });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Preview recovery' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(page.getByText('connected', { exact: true })).toBeVisible(); };
  try {
    await page.goto('/'); await page.getByLabel('Recover artifact previews, Backlog', { exact: true }).dblclick(); const task = page.getByRole('dialog', { name: 'Recover artifact previews', exact: true });
    const description = task.getByRole('textbox', { name: 'Description', exact: true }); await description.fill('Keep this unsaved task scope'); await description.evaluate((node) => { node.dataset.owner = 'task-draft'; });
    await task.getByRole('tab', { name: 'Evidence', exact: true }).click(); expect(textReads).toBe(0);
    await task.getByRole('button', { name: 'Preview artifact legacy.LOG', exact: true }).click(); const preview = page.getByRole('dialog', { name: 'Preview: legacy.LOG', exact: true });
    await expect.poll(() => held).toBe(1); await expect(preview.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(preview.getByRole('status')).toHaveCount(0);
    await preview.getByRole('button', { name: 'Retry file preview', exact: true }).click(); await expect(preview.locator('pre')).toHaveText('Actual persisted evidence\nsecond line'); release(); await expect(preview.getByText('Obsolete delayed body', { exact: true })).toHaveCount(0);
    await preview.locator('pre').evaluate((node) => { node.dataset.owner = 'reading'; const selection = window.getSelection(); const range = document.createRange(); range.setStart(node.firstChild!, 0); range.setEnd(node.firstChild!, 6); selection?.removeAllRanges(); selection?.addRange(range); });
    const download = preview.getByRole('link', { name: 'Download file', exact: true }); await download.focus(); hold = true; await reconnect(); await expect.poll(() => held).toBe(2);
    await expect(preview.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(preview.locator('pre')).toHaveAttribute('data-owner', 'reading'); await expect(preview.locator('pre')).toHaveText('Actual persisted evidence\nsecond line'); await expect(download).toBeFocused(); expect(await page.evaluate(() => window.getSelection()?.toString())).toBe('Actual');
    await page.setViewportSize({ width: 760, height: 600 }); await page.screenshot({ path: test.info().outputPath('artifact-preview-retained-timeout.png') });
    await preview.getByRole('button', { name: 'Retry file preview', exact: true }).click(); await expect(preview.getByRole('alert')).toHaveCount(0); release(); await page.keyboard.press('Escape'); await expect(preview).toHaveCount(0); await expect(task).toBeVisible();
    await expect(description).toHaveValue('Keep this unsaved task scope'); await expect(description).toHaveAttribute('data-owner', 'task-draft');
    const count = textReads; await reconnect(); expect(textReads).toBe(count);
    await task.getByRole('button', { name: 'Preview artifact legacy.PNG', exact: true }).click(); const image = page.getByRole('dialog', { name: 'Preview: legacy.PNG', exact: true }); await expect(image.getByRole('alert')).toContainText('Could not load this image');
    await image.getByRole('button', { name: 'Retry image preview', exact: true }).click(); await expect.poll(() => image.getByRole('img').evaluate((node: HTMLImageElement) => node.naturalWidth)).toBe(1); expect(imageReads).toBe(2); await expect(image.getByRole('alert')).toHaveCount(0);
    await page.keyboard.press('Escape'); await task.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(task).toHaveCount(0); expect(((await command(request, { cmd: 'task_detail', id })).task as Row).description).toBe('Saved task scope');
  } finally { release(); }
});
