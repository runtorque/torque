import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); return result.data; }
test('Agent Settings recovers read and partial-save timeouts without replay, losing drafts or replacing acknowledged fields', async ({ page, request }) => {
  test.setTimeout(100_000); const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Agent settings outcomes ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
  const id = String((await command(request, { cmd: 'add_engineer', name: group, group, provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
  let holdRead = true; let readHeld = 0; let releaseRead = () => {}; let holdWrite = true; let writeHeld = false; let releaseWrite = () => {}; let socket: WebSocketRoute | undefined; let connections = 0; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'get_agent_settings' && holdRead) { const response = await route.fetch(); readHeld++; await new Promise<void>((resolve) => { releaseRead = resolve; }); await route.fulfill({ response }); return; }
    if (['update_agent_settings', 'update_agent_digest_settings', 'relaunch_agent'].includes(String(data.cmd))) writes.push(data);
    if (data.cmd === 'update_agent_digest_settings' && holdWrite) { writeHeld = true; await new Promise<void>((resolve) => { releaseWrite = resolve; }); await route.fulfill({ json: { ok: false, error: 'Late obsolete digest refusal' } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Agent settings', exact: true }); const save = dialog.getByRole('button', { name: 'Save settings', exact: true }); const instructions = dialog.getByRole('textbox', { name: 'Custom instructions', exact: true }); const heartbeat = dialog.getByLabel('Heartbeat interval (seconds)', { exact: true });
    await expect.poll(() => readHeld).toBeGreaterThan(0); await expect(dialog.getByRole('button', { name: 'Retry settings', exact: true })).toBeVisible({ timeout: 20_000 }); await expect(save).toBeDisabled(); holdRead = false; await dialog.getByRole('button', { name: 'Retry settings', exact: true }).click(); await expect(dialog.getByText('Refreshing agent settings…')).toHaveCount(0); releaseRead();
    await instructions.fill('Retained timeout draft'); await instructions.evaluate((node: HTMLTextAreaElement) => { node.dataset.agentOutcomeAnchor = 'same'; node.setSelectionRange(1, 5); });
    holdRead = true; const before = connections; await socket!.close({ code: 1012, reason: 'Agent settings timeout refresh' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(instructions).toBeFocused(); await expect(instructions).toHaveAttribute('data-agent-outcome-anchor', 'same'); expect(await instructions.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 5]);
    holdRead = false; releaseRead(); await dialog.getByRole('button', { name: 'Retry settings', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); await heartbeat.fill('0'); await save.click(); await expect.poll(() => writeHeld).toBe(true); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled();
    await expect(dialog.getByRole('alert')).toContainText('Some changes were saved.', { timeout: 35_000 }); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown'); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeEnabled();
    await command(request, { cmd: 'update_agent_settings', agent_id: id, settings: { custom_instructions: 'External acknowledged update' } }); await expect(instructions).toHaveValue('External acknowledged update'); await expect(heartbeat).toHaveValue('0');
    const previous = connections; await socket!.close({ code: 1012, reason: 'Agent settings unknown outcome' }); await expect.poll(() => connections).toBeGreaterThan(previous); await expect(save).toBeEnabled(); expect(writes).toHaveLength(2);
    holdWrite = false; await save.click(); await expect(dialog).toHaveCount(0); releaseWrite(); expect(writes.map((data) => data.cmd)).toEqual(['update_agent_settings', 'update_agent_digest_settings', 'update_agent_digest_settings']);
    const saved = (await command(request, { cmd: 'get_agent_settings', agent_id: id })).resolved as Row; expect(saved).toMatchObject({ custom_instructions: { value: 'External acknowledged update' }, heartbeat_interval: { value: 0 } });
    await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(instructions).toHaveValue('External acknowledged update'); await expect(heartbeat).toHaveValue('0'); await expect(save).toBeDisabled();
  } finally { releaseRead(); releaseWrite(); await command(request, { cmd: 'remove_agent', id }); }
});
