import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
let created: string[] = [];
test.afterEach(async ({ request }) => { for (const id of created) await command(request, { cmd: 'remove_agent', id }); created = []; });
test('Agent settings refresh preserves edits and resets; partial saves retry only unfinished scopes', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Agent settings ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'engineer_update_settings', group, engineer_model: 'group-model', heartbeat_interval: 300 });
  const id = String((await command(request, { cmd: 'add_engineer', name: group, group, provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id); created.push(id);
  await command(request, { cmd: 'update_agent_settings', agent_id: id, settings: { model: 'override' } });
  let socket: WebSocketRoute | undefined; let connections = 0; let reads = 0; let refuseDigest = true; let refuseRead = false; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'get_agent_settings') { reads++; if (refuseRead) { await route.fulfill({ json: { ok: false, error: 'Injected read refusal' } }); return; } }
    if (['update_agent_settings', 'update_agent_digest_settings', 'set_engineer_specializations'].includes(String(data.cmd))) writes.push(data);
    if (refuseDigest && data.cmd === 'update_agent_digest_settings') await route.fulfill({ json: { ok: false, error: 'Injected digest refusal' } }); else await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Agent settings', exact: true });
  const model = dialog.getByRole('textbox', { name: 'Model', exact: true }); const instructions = dialog.getByLabel('Custom instructions', { exact: true }); const heartbeat = dialog.getByLabel('Heartbeat interval (seconds)', { exact: true }); const save = dialog.getByRole('button', { name: 'Save settings', exact: true });
  await expect(model).toHaveValue('override'); await expect(save).toBeDisabled(); await expect.poll(() => reads).toBeGreaterThan(0);
  await model.locator('..').getByRole('button', { name: 'Use inherited' }).click(); await instructions.fill('Local instructions draft'); await instructions.focus(); await instructions.evaluate((input: HTMLTextAreaElement) => { input.setSelectionRange(2, 7); input.dataset.agentSettingsAnchor = 'original'; });
  const body = dialog.locator('[data-dialog-body-layout]'); const scrollBefore = await body.evaluate((node) => node.scrollTop);
  await command(request, { cmd: 'engineer_update_settings', group, engineer_model: 'new-group-model', heartbeat_interval: 600 });
  await expect(model).toHaveValue('new-group-model'); await expect(heartbeat).toHaveValue('600'); await expect(instructions).toHaveValue('Local instructions draft'); await expect(instructions).toBeFocused();
  const before = connections; await socket!.close({ code: 1012, reason: 'Per-agent draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(dialog.getByText('Refreshing agent settings…')).toHaveCount(0);
  await expect(instructions).toBeFocused(); await expect(instructions).toHaveAttribute('data-agent-settings-anchor', 'original'); expect(await instructions.evaluate((input: HTMLTextAreaElement) => [input.selectionStart, input.selectionEnd])).toEqual([2, 7]); await expect(model).toHaveValue('new-group-model'); expect(await body.evaluate((node) => node.scrollTop)).toBe(scrollBefore);
  await page.keyboard.press('Escape'); const discard = page.getByRole('dialog', { name: 'Discard agent settings changes?' }); await expect(discard).toBeVisible(); await discard.getByRole('button', { name: 'Keep editing' }).click(); await expect(instructions).toHaveValue('Local instructions draft');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await discard.getByRole('button', { name: 'Keep editing' }).click();
  await page.mouse.click(3, 3); await expect(discard).toBeVisible(); await discard.getByRole('button', { name: 'Keep editing' }).click();
  refuseRead = true; await dialog.getByRole('button', { name: 'Refresh settings', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('Injected read refusal'); await expect(instructions).toHaveValue('Local instructions draft');
  refuseRead = false; await dialog.getByRole('button', { name: 'Retry settings' }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0);
  await heartbeat.fill('0'); await save.click(); await expect(dialog.getByRole('alert')).toHaveText('Some changes were saved. Injected digest refusal'); await expect(dialog.getByRole('alert')).toBeFocused();
  expect(writes).toEqual([{ cmd: 'update_agent_settings', agent_id: id, settings: { model: null, custom_instructions: 'Local instructions draft' } }, { cmd: 'update_agent_digest_settings', agent_id: id, settings: { heartbeat_interval: 0 } }]);
  await command(request, { cmd: 'update_agent_settings', agent_id: id, settings: { model: 'external-model', custom_instructions: 'External instructions' } }); await expect(model).toHaveValue('external-model'); await expect(instructions).toHaveValue('External instructions'); await expect(heartbeat).toHaveValue('0');
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('agent-settings-partial.png') });
  refuseDigest = false; await save.click(); await expect(dialog).toHaveCount(0); expect(writes.map((data) => data.cmd)).toEqual(['update_agent_settings', 'update_agent_digest_settings', 'update_agent_digest_settings']);
  const resolved = (await command(request, { cmd: 'get_agent_settings', agent_id: id })).resolved as Row;
  expect(resolved).toMatchObject({ model: { value: 'external-model', origin: 'per-agent' }, custom_instructions: { value: 'External instructions', origin: 'per-agent' }, heartbeat_interval: { value: 0, origin: 'per-agent' } });
  await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(model).toHaveValue('external-model'); await expect(heartbeat).toHaveValue('0'); await expect(save).toBeDisabled();
  await page.setViewportSize({ width: 720, height: 760 }); await instructions.fill('Discard this'); await dialog.getByRole('button', { name: 'Close dialog' }).click(); await expect(discard).toBeVisible(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('agent-settings-discard.png') }); await discard.getByRole('button', { name: 'Discard changes' }).click(); await expect(dialog).toHaveCount(0);
  const hiddenReads = reads; const beforeHidden = connections; await socket!.close({ code: 1012, reason: 'Hidden settings reconnect' }); await expect.poll(() => connections).toBeGreaterThan(beforeHidden); expect(reads).toBe(hiddenReads);
});
