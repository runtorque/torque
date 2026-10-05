import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
test('deployment status uses scoped evidence, retains drafts through reconnect, and recovers from stale and expired reads', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const groups = [`Deploy primary ${Date.now()}`, `Deploy second ${Date.now()}`]; const reads: Row[] = []; let mode = 'success'; let count = 2; let release = () => {}; let socket: WebSocketRoute | undefined; let connections = 0; let taskId = '';
  await page.routeWebSocket(/\/ws\?/, (route) => { socket = route; connections++; route.connectToServer(); });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'get_deploy_state') { await route.continue(); return; }
    reads.push(data); const ownMode = mode; const ownCount = data.group === groups[1] ? 5 : count;
    if (ownMode === 'hold') { mode = 'success'; await new Promise<void>((resolve) => { release = resolve; }); }
    await route.fulfill({ json: { ok: true, data: { type: 'deploy_state', group: data.group, error: ownMode === 'failure' ? 'QA repository unavailable' : '', pending_deploy: { count: ownMode === 'hold' ? 99 : ownMode === 'zero' ? 0 : ownCount, torque_task_ids: ['TORQUE:901', 'TORQUE:902'] }, daemon_uptime_seconds: 300 } } });
  });
  try {
    for (const group of groups) await command(request, { cmd: 'add_group', group });
    const actual = await command(request, { cmd: 'get_deploy_state', group: groups[0] }); expect(actual.type).toBe('deploy_state'); expect(actual.group).toBe(groups[0]); expect(actual.pending_deploy).toHaveProperty('count'); expect(actual.pending_deploy).toHaveProperty('torque_task_ids');
    taskId = String((await command(request, { cmd: 'board_add_task', group: groups[0], task: 'Retain deployment draft', description: 'Saved description', lane: 'Backlog' })).task_id);
    await command(request, { cmd: 'ui_select_group', group: groups[0] }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } }); await page.goto('/');
    const chip = page.getByRole('button', { name: 'Deploy +2', exact: true }); await expect(chip).toHaveAttribute('title', /TORQUE:901/); await chip.click(); await expect(page.getByRole('heading', { name: 'Board', exact: true })).toBeVisible();
    await page.getByText('Retain deployment draft', { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: 'Retain deployment draft', exact: true }); const draft = dialog.getByRole('textbox', { name: 'Description', exact: true }); await draft.fill('Keep during deployment refresh'); await draft.focus(); await draft.evaluate((node: HTMLTextAreaElement) => { node.dataset.owner = 'same'; node.setSelectionRange(2, 8); });
    mode = 'failure'; const before = connections; await socket!.close({ code: 1012, reason: 'Deploy status reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(page.locator('footer[aria-label="Workspace status"] button').filter({ hasText: 'Deploy ?' })).toHaveAttribute('title', /QA repository unavailable/);
    await expect(draft).toHaveValue('Keep during deployment refresh'); await expect(draft).toBeFocused(); await expect(draft).toHaveAttribute('data-owner', 'same'); expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); mode = 'success'; count = 3; await page.getByRole('button', { name: 'Deploy ?', exact: true }).click(); await expect(page.getByRole('button', { name: 'Deploy +3' })).toBeVisible();
    mode = 'hold'; const initialReads = reads.length; await page.getByRole('button', { name: 'Deploy +3' }).click(); await expect.poll(() => reads.length).toBe(initialReads + 1);
    await page.getByRole('button', { name: groups[1]!, exact: true }).click(); await expect(page.getByRole('button', { name: 'Deploy +5' })).toBeVisible(); release(); await expect(page.getByRole('button', { name: 'Deploy +99' })).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('scoped-deployment-status.png'), animations: 'disabled' });
    mode = 'hold'; await page.getByRole('button', { name: 'Deploy +5' }).click(); await expect(page.getByRole('button', { name: 'Deploy ?', exact: true })).toHaveAttribute('title', /timed out/, { timeout: 20_000 });
    mode = 'zero'; await page.getByRole('button', { name: 'Deploy ?', exact: true }).click(); await expect(page.locator('footer[aria-label="Workspace status"]').getByRole('button', { name: /^Deploy / })).toHaveCount(0); release(); await expect(page.locator('footer[aria-label="Workspace status"]').getByRole('button', { name: /^Deploy / })).toHaveCount(0);
    expect(reads.every((row) => groups.includes(String(row.group)))).toBe(true); await writeFile(test.info().outputPath('deployment-status-reads.json'), JSON.stringify({ actual, reads }, null, 2));
  } finally { release(); if (taskId) await command(request, { cmd: 'board_remove_task', id: taskId }); for (const group of groups) await command(request, { cmd: 'remove_group', group }); }
});
