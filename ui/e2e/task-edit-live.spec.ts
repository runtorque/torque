import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
test.use({ timezoneId: 'America/Sao_Paulo' });
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('existing-task preview uses the draft and rejected saves retain it until acknowledgement', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-task-edit-e2e-'));
  const group = `Task edit ${Date.now()}`; const prefix = `edit-${Date.now()}`;
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, board_sync_enabled: false, git_worktree: false } });
    await command(request, { cmd: 'save_action', group, name: 'edit/build', scope: 'project', action: { prompt: 'TASK={{ TASK }}\nTITLE={{ torque.task.title }}\nDESCRIPTION=[{{ torque.task.description }}]\nROLE={{ torque.agent.role }}\nSCOPE={{ SCOPE }}' } });
    for (const role of ['saved-reviewer', 'draft-reviewer']) await command(request, { cmd: 'save_role', group, name: role, scope: 'project', data: { name: role, preamble: `PREAMBLE ${role}` } });
    const synthetic = await (await request.post('/api/profile/synthetic_agents', { data: { group, prefix, count: 1, directory, agent_type: 'generic' } })).json() as { ok: boolean; data: { agent_ids: string[] } };
    expect(synthetic.ok).toBe(true); const agentId = synthetic.data.agent_ids[0]!;
    const created = await command(request, { cmd: 'board_add_task', group, task: 'Saved edit fixture', description: 'Saved description must disappear', action_name: 'edit/build', agent_template: 'saved-reviewer', action_vars: { SCOPE: 'saved' }, agent_id: agentId, dispatch_state: 'live', lane: 'Backlog', scheduled_at: '2026-10-01T12:30:45Z', labels: ['original'] });
    const id = String(created.task_id);
    await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
    await page.getByText('Saved edit fixture', { exact: true }).dblclick();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('Scheduled for', { exact: true })).toHaveValue('2026-10-01T09:30');
    await dialog.getByRole('textbox', { name: 'Title', exact: true }).fill('Unsaved title');
    await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('');
    await dialog.getByRole('combobox', { name: 'Worker role', exact: true }).selectOption('draft-reviewer');
    await dialog.getByRole('textbox', { name: 'SCOPE', exact: true }).fill('draft');
    await dialog.getByRole('button', { name: 'Preview prompt', exact: true }).click();
    const preview = dialog.locator('details').filter({ has: page.getByText('Rendered prompt preview', { exact: true }) }).locator('pre');
    await expect(preview).toContainText('TASK=Unsaved title'); await expect(preview).toContainText('TITLE=Unsaved title');
    await expect(preview).toContainText('DESCRIPTION=[]'); await expect(preview).toContainText('ROLE=draft-reviewer');
    await expect(preview).toContainText('PREAMBLE draft-reviewer'); await expect(preview).toContainText('SCOPE=draft');
    expect((await command(request, { cmd: 'task_detail', id })).task).toMatchObject({ task: 'Saved edit fixture', description: 'Saved description must disappear', agent_template: 'saved-reviewer', action_vars: { SCOPE: 'saved' } });
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('Task has active work in its assigned worker');
    await expect(dialog.getByRole('textbox', { name: 'Title', exact: true })).toHaveValue('Unsaved title');
    await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue('');
    // Remove only our synthetic worker. The open editor must not reassign it on save.
    const removed = await request.post('/api/profile/synthetic_agents', { data: { group, prefix, count: 0 } }); expect(removed.ok()).toBe(true);
    let release!: () => void; const hold = new Promise<void>((resolve) => { release = resolve; });
    const writes: Row[] = [];
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'board_update_task') { writes.push(data); const response = await route.fetch(); await hold; await route.fulfill({ response }); }
      else await route.continue();
    });
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click();
    await expect(dialog.getByRole('button', { name: 'Saving task…', exact: true })).toBeDisabled();
    await dialog.getByRole('button', { name: 'Close dialog', exact: true }).click();
    await page.keyboard.press('Escape'); await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('textbox', { name: 'Title', exact: true })).toBeDisabled();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toEqual({ cmd: 'board_update_task', id, task: 'Unsaved title', description: '', agent_template: 'draft-reviewer', action_vars: { SCOPE: 'draft' }, enforce_dispatch_edit_gate: true });
    release(); await expect(dialog).toHaveCount(0);
    const saved = (await command(request, { cmd: 'task_detail', id })).task as Row;
    expect(saved).toMatchObject({ task: 'Unsaved title', description: '', agent_template: 'draft-reviewer', action_vars: { SCOPE: 'draft' }, scheduled_at: '2026-10-01T12:30:45Z', labels: ['original'] });
    await page.getByText('Unsaved title', { exact: true }).dblclick();
    await page.setViewportSize({ width: 760, height: 600 });
    await expect(page.getByRole('button', { name: 'Save task', exact: true })).toBeInViewport();
    await page.getByRole('textbox', { name: 'Description', exact: true }).fill('Visible draft at narrow viewport');
    await page.screenshot({ path: test.info().outputPath('task-edit.png'), fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(((await command(request, { cmd: 'task_detail', id })).task as Row).description).toBe('');
  } finally {
    await request.post('/api/profile/synthetic_agents', { data: { group, prefix, count: 0 } });
    await rm(directory, { recursive: true, force: true });
  }
});

test('attachment cleanup follows acknowledged editing and can retry without repeating the save', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Attachment edit ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  const created = await command(request, { cmd: 'board_add_task', group, task: 'Attachment cleanup fixture', lane: 'Backlog' });
  const id = String(created.task_id);
  const uploaded = await (await request.post('/api/upload', { multipart: { task_id: id, file: { name: 'keep-until-saved.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGzQAAAAASUVORK5CYII=', 'base64') } } })).json() as { ok: boolean; data: Row[] };
  expect(uploaded.ok).toBe(true);
  await command(request, { cmd: 'board_update_task', id, attachments: uploaded.data });
  const url = `/attachments/${encodeURIComponent(id)}/${encodeURIComponent(String(uploaded.data[0]!.filename))}`;
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByText('Attachment cleanup fixture', { exact: true }).dblclick();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await dialog.getByRole('button', { name: 'Remove', exact: true }).click();
  let failure = 'board_update_task'; const calls: string[] = [];
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row; calls.push(String(data.cmd));
    if (data.cmd === failure) await route.fulfill({ json: { ok: false, error: 'Injected edit failure' } });
    else await route.continue();
  });
  await dialog.getByRole('button', { name: 'Save task', exact: true }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Injected edit failure');
  expect(calls).toEqual(['board_update_task']); expect((await request.get(url)).status()).toBe(200);
  expect(((await command(request, { cmd: 'task_detail', id })).task as Row).attachments).toHaveLength(1);
  failure = 'remove_attachment'; await dialog.getByRole('button', { name: 'Save task', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('Task changes saved, but attachment cleanup failed.');
  expect(calls).toEqual(['board_update_task', 'board_update_task', 'remove_attachment']); expect((await request.get(url)).status()).toBe(200);
  expect(((await command(request, { cmd: 'task_detail', id })).task as Row).attachments).toHaveLength(0);
  failure = ''; await dialog.getByRole('button', { name: 'Save task', exact: true }).click();
  await expect(dialog).toHaveCount(0); expect((await request.get(url)).status()).toBe(404);
  expect(calls).toEqual(['board_update_task', 'board_update_task', 'remove_attachment', 'remove_attachment']);
});
