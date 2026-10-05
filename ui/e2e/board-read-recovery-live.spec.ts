import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function prepare(request: APIRequestContext, title: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `${title} ${Date.now()}`; await command(request, { cmd: 'add_group', group });
  const result = await command(request, { cmd: 'board_add_task', group, task: title, lane: 'Backlog' });
  return { group, id: String(result.task_id) };
}
test('Board activity keeps its reading window and draft through a real deadline, wrong identity and explicit retry', async ({ page, request }) => {
  test.setTimeout(60_000); const title = 'Recover Board activity'; const { group, id } = await prepare(request, title);
  const messages = Array.from({ length: 85 }, (_, index) => ({ message: `Reading event ${index + 1}`, action: 'progress', timestamp: 1700000000 + index }));
  await command(request, { cmd: 'board_update_task', id, messages });
  await page.goto('/'); await page.getByRole('button', { name: /▦ Board/ }).click(); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByRole('button', { name: `Actions for ${title}`, exact: true }).click(); await page.getByRole('menuitem', { name: 'Task activity', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: title, exact: true }); const activity = dialog.getByRole('region', { name: 'Task activity', exact: true }); const rows = activity.getByRole('article');
  await expect(rows).toHaveCount(40); await dialog.getByRole('button', { name: 'Load older activity · 45 remaining', exact: true }).click(); await expect(rows).toHaveCount(80);
  const draft = dialog.getByRole('textbox', { name: 'Description', exact: true }); await draft.fill('Keep this unsaved description'); await draft.focus();
  await draft.evaluate((node: HTMLTextAreaElement) => { node.dataset.owner = 'retained'; node.setSelectionRange(2, 8); });
  await rows.first().evaluate((node) => { (node as HTMLElement).dataset.anchor = 'retained'; });
  // Activity and selected-task evidence own separate refreshes. Hold both,
  // then inject the wrong identity only for the explicit Activity retry.
  let phase: 'hold' | 'wrong' | 'pass' = 'hold'; const releases: (() => void)[] = []; let held = 0;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'task_detail' || data.id !== id) { await route.continue(); return; }
    if (phase === 'hold') { held++; const response = await route.fetch(); await new Promise<void>((resolve) => { releases.push(resolve); }); await route.fulfill({ response }); return; }
    if (phase === 'wrong') { await route.fulfill({ json: { ok: true, data: { type: 'task_detail', id: 'unrelated', task: { id: 'unrelated', messages: [{ message: 'Unrelated activity' }] } } } }); return; }
    await route.continue();
  });
  try {
    await command(request, { cmd: 'board_update_task', id, messages: [...messages, { message: 'New arrival' }] }); await expect.poll(() => held).toBeGreaterThan(0);
    await expect(activity.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(rows).toHaveCount(80); await expect(rows.first()).toHaveAttribute('data-anchor', 'retained');
    await expect(draft).toHaveValue('Keep this unsaved description'); await expect(draft).toBeFocused(); await expect(draft).toHaveAttribute('data-owner', 'retained');
    expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await page.screenshot({ path: test.info().outputPath('board-activity-timeout.png'), animations: 'disabled' });
    phase = 'wrong'; await activity.getByRole('button', { name: 'Retry activity', exact: true }).click(); await expect(activity.getByRole('alert')).toContainText('Could not refresh activity'); await expect(dialog.getByText('Unrelated activity')).toHaveCount(0);
    phase = 'pass'; await activity.getByRole('button', { name: 'Retry activity', exact: true }).click(); await expect(activity.getByRole('alert')).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Show 1 new messages', exact: true })).toBeVisible(); releases.forEach((release) => release()); await expect(rows.first()).toHaveAttribute('data-anchor', 'retained');
    await activity.getByRole('button', { name: 'Show 1 new messages', exact: true }).click(); await expect(rows.first()).toContainText('New arrival');
    await dialog.getByRole('button', { name: 'Retry task details', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); await expect(rows.first()).toContainText('New arrival'); await expect(draft).toHaveValue('Keep this unsaved description');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); expect(((await command(request, { cmd: 'task_detail', id })).task as Row).description).toBe('');
  } finally { releases.forEach((release) => release()); }
});
test('Board prompt preview releases a real deadline and renders the explicitly retried current draft', async ({ page, request }) => {
  test.setTimeout(60_000); const title = 'Recover Board preview'; const { group, id } = await prepare(request, title);
  const directory = await mkdtemp(join(tmpdir(), 'torque-board-read-')); let release = () => {}; let held = false; const previews: Row[] = [];
  try {
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory } });
    await command(request, { cmd: 'save_action', group, name: 'recovery/preview', scope: 'project', action: { prompt: 'TASK={{ TASK }}' } });
    await command(request, { cmd: 'board_update_task', id, action_name: 'recovery/preview' });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'preview_prompt') { previews.push(data); if (!held) { held = true; const response = await route.fetch(); await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response }); return; } }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /▦ Board/ }).click(); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByText(title, { exact: true }).dblclick();
    const dialog = page.getByRole('dialog'); const draft = dialog.getByLabel('Title', { exact: true }); await draft.fill('First unsaved preview');
    await dialog.getByRole('button', { name: 'Preview prompt', exact: true }).click(); await expect.poll(() => held).toBe(true);
    await draft.fill('Current unsaved preview'); await draft.focus(); await draft.evaluate((node: HTMLInputElement) => { node.dataset.owner = 'retained'; node.setSelectionRange(1, 7); });
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(draft).toBeFocused(); await expect(draft).toHaveAttribute('data-owner', 'retained');
    expect(await draft.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 7]);
    await page.screenshot({ path: test.info().outputPath('board-preview-timeout.png') });
    await dialog.getByRole('button', { name: 'Preview prompt', exact: true }).click(); await expect(dialog.locator('pre')).toContainText('TASK=Current unsaved preview'); release();
    await expect(dialog.locator('pre')).toContainText('TASK=Current unsaved preview'); expect(previews).toHaveLength(2); expect(previews[1]?.task).toBe('Current unsaved preview');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); expect(((await command(request, { cmd: 'task_detail', id })).task as Row).task).toBe(title);
  } finally { release(); await rm(directory, { recursive: true, force: true }); }
});
