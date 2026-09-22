import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
async function fixture(request: APIRequestContext, title: string) {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `${title} ${Date.now()}`; await command(request, { cmd: 'add_group', group });
  const result = await command(request, { cmd: 'board_add_task', group, task: title, lane: 'Backlog' });
  return { group, id: String(result.task_id) };
}
test('structured evidence stages edits, survives nested preview and persists only on successful task save', async ({ page, request }) => {
  const { group, id } = await fixture(request, 'Structured evidence');
  await command(request, { cmd: 'board_update_task', id, artifacts: [{ id: 'source', type: 'snippet', title: 'Source', content: 'saved text', prompt: { mode: 'inline' } }, { id: 'external', type: 'file_ref', title: 'External source', path: '/project/source.ts', lifecycle: { owner: 'agent' } }] });
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByText('Structured evidence', { exact: true }).dblclick();
  const dialog = page.getByRole('dialog', { name: 'Structured evidence', exact: true });
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await dialog.getByRole('button', { name: 'Edit artifact Source', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Artifact title', exact: true }).fill('Discarded edit');
  await dialog.getByRole('button', { name: 'Save artifact', exact: true }).click();
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(((await command(request, { cmd: 'task_detail', id })).task as Row).artifacts).toEqual(expect.arrayContaining([expect.objectContaining({ title: 'Source' })]));
  await page.getByText('Structured evidence', { exact: true }).dblclick();
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await dialog.getByRole('button', { name: 'Edit artifact Source', exact: true }).click();
  await dialog.getByRole('combobox', { name: 'Artifact type', exact: true }).selectOption('diff');
  await dialog.getByRole('textbox', { name: 'Artifact title', exact: true }).fill('Reviewed diff');
  await dialog.getByRole('textbox', { name: 'Artifact summary', exact: true }).fill('Changed lines');
  await dialog.getByRole('textbox', { name: 'Artifact path', exact: true }).fill('/project/example.patch');
  await dialog.getByRole('spinbutton', { name: 'Line start', exact: true }).fill('2'); await dialog.getByRole('spinbutton', { name: 'Line end', exact: true }).fill('4');
  await dialog.getByRole('combobox', { name: 'Prompt mode', exact: true }).selectOption('inline');
  await dialog.getByRole('textbox', { name: 'Artifact content', exact: true }).fill('- old\n+ new <script>');
  await dialog.getByRole('tab', { name: 'Activity', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeDisabled();
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'Artifact content', exact: true })).toHaveValue('- old\n+ new <script>');
  await dialog.getByRole('button', { name: 'Save artifact', exact: true }).click();
  await dialog.getByRole('button', { name: 'Preview artifact Reviewed diff', exact: true }).click();
  const preview = page.getByRole('dialog', { name: 'Preview: Reviewed diff', exact: true });
  await expect(preview.locator('pre')).toHaveText('- old\n+ new <script>'); await expect(preview.getByText('Lines 2–4')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(preview).toHaveCount(0); await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Remove artifact External source', exact: true }).click();
  let reject = true; const writes: Row[] = [];
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'board_update_task') { writes.push(data); if (reject) { await route.fulfill({ json: { ok: false, error: 'Injected save failure' } }); return; } }
    await route.continue();
  });
  await dialog.getByRole('button', { name: 'Save task', exact: true }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Injected save failure');
  await expect(dialog.getByRole('button', { name: 'Edit artifact Reviewed diff', exact: true })).toBeVisible();
  reject = false; await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect(dialog).toHaveCount(0);
  expect(writes).toHaveLength(2);
  const artifacts = ((await command(request, { cmd: 'task_detail', id })).task as Row).artifacts as Row[];
  expect(artifacts).toHaveLength(1); expect(artifacts[0]).toMatchObject({ type: 'diff', title: 'Reviewed diff', summary: 'Changed lines', path: '/project/example.patch', line_start: 2, line_end: 4, content: '- old\n+ new <script>', prompt: { mode: 'inline' } });
  await page.getByText('Structured evidence', { exact: true }).dblclick(); await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await page.setViewportSize({ width: 760, height: 600 });
  await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeInViewport();
  await dialog.getByRole('button', { name: 'Edit artifact Reviewed diff', exact: true }).click();
  const evidenceBox = await dialog.getByRole('tabpanel', { name: 'Evidence', exact: true }).boundingBox(); expect(evidenceBox!.height).toBeGreaterThan(160);
  await expect(dialog.getByRole('textbox', { name: 'Artifact title', exact: true })).toBeInViewport();
  await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeInViewport();
  await page.screenshot({ path: test.info().outputPath('evidence-editor.png'), fullPage: true, animations: 'disabled' });
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
});

test('uploaded logs are artifacts, images preview in place and Cancel deletes only unsaved uploads', async ({ page, request }) => {
  const { group, id } = await fixture(request, 'Upload evidence');
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByText('Upload evidence', { exact: true }).dblclick();
  const dialog = page.getByRole('dialog', { name: 'Upload evidence', exact: true });
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await dialog.getByLabel('Upload evidence files', { exact: true }).setInputFiles([{ name: 'worker.log', mimeType: 'text/plain', buffer: Buffer.from('real log content') }, { name: 'image.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGzQAAAAASUVORK5CYII=', 'base64') }]);
  await expect(dialog.getByRole('button', { name: 'Edit artifact worker.log', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Preview attachment image.png', exact: true }).click();
  const preview = page.getByRole('dialog', { name: 'Preview: image.png', exact: true });
  await expect(preview.getByRole('img')).toBeVisible();
  await expect.poll(() => preview.getByRole('img').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(1);
  await preview.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await dialog.getByRole('button', { name: 'Preview artifact worker.log', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Preview: worker.log', exact: true }).locator('pre')).toHaveText('real log content');
  await page.keyboard.press('Escape');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(dialog).toHaveCount(0);
  expect((await request.get(`/attachments/${id}/worker.log`)).status()).toBe(404); expect((await request.get(`/attachments/${id}/image.png`)).status()).toBe(404);
  const saved = (await command(request, { cmd: 'task_detail', id })).task as Row; expect(saved.artifacts).toHaveLength(0); expect(saved.attachments).toHaveLength(0);
});

test('activity card entry hydrates messages with actor and dates and holds the reading window across appends', async ({ page, request }) => {
  const { group, id } = await fixture(request, 'Activity evidence');
  const messages = Array.from({ length: 85 }, (_, index) => ({ action: 'progress', agent: 'Worker QA', message: `Activity message ${index + 1}`, timestamp: 1700000000 + index }));
  await command(request, { cmd: 'board_update_task', id, messages });
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByRole('button', { name: 'Actions for Activity evidence', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Task activity', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Activity evidence', exact: true });
  await expect(dialog.getByRole('tab', { name: 'Activity', exact: true })).toHaveAttribute('aria-selected', 'true');
  const rows = dialog.getByRole('article'); await expect(rows).toHaveCount(40);
  await expect(rows.first()).toHaveText(/#85progressWorker QA.*Activity message 85/);
  await expect(rows.first().locator('time')).toHaveAttribute('datetime', '2023-11-14T22:14:44.000Z');
  await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('Retained activity draft');
  await dialog.getByRole('button', { name: 'Load older activity · 45 remaining', exact: true }).click();
  await expect(rows).toHaveCount(80);
  await command(request, { cmd: 'board_update_task', id, messages: [...messages, { action: 'done', agent: 'Worker QA', message: 'Newest event', timestamp: 1700000085 }] });
  await expect(dialog.getByRole('button', { name: 'Show 1 new messages', exact: true })).toBeVisible();
  await expect(rows.first()).toContainText('Activity message 85');
  await dialog.getByRole('tab', { name: 'Execution', exact: true }).click(); await dialog.getByRole('tab', { name: 'Activity', exact: true }).click();
  await expect(rows).toHaveCount(80); await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue('Retained activity draft');
  await dialog.getByRole('button', { name: 'Show 1 new messages', exact: true }).click(); await expect(rows.first()).toContainText('Newest event');
  await page.screenshot({ path: test.info().outputPath('task-activity.png'), fullPage: true, animations: 'disabled' });
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(((await command(request, { cmd: 'task_detail', id })).task as Row).description).toBe('');
});
