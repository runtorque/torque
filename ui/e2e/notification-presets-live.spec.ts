import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
const quiet = { digest_verbosity: 'compact', push_interval: 120, max_interval: 600, heartbeat_interval: 0, enabled_events: ['task_derived', 'task_health_alert'] };
const normal = { digest_verbosity: 'balanced', push_interval: 60, max_interval: 300, heartbeat_interval: 300, enabled_events: ['agent_started', 'task_dispatched', 'task_derived', 'task_health_alert'] };
const noisy = { digest_verbosity: 'detailed', push_interval: 30, max_interval: 120, heartbeat_interval: 60, enabled_events: ['agent_started', 'task_dispatched', 'task_derived', 'agent_progress', 'task_health_alert'] };
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); return result.data; }
test('Engineer notification presets persist group and per-agent values and preserve explicit empty creation events', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Notification presets ${Date.now()}`; let agent = '';
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } }); await command(request, { cmd: 'engineer_update_settings', group, ...normal, custom_instructions: 'Keep group instructions' }); await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
    let socket: WebSocketRoute | undefined; let connections = 0; let refuseGroup = true; let refuseAgent = true; const writes: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'add_engineer') { writes.push(data); const result = await route.fetch(); const body = await result.json() as { ok: boolean; data: Row }; if (body.ok) agent = String(body.data.id); await route.fulfill({ response: result }); return; }
      if (['engineer_update_settings', 'update_agent_digest_settings', 'relaunch_agent'].includes(String(data.cmd))) writes.push(data);
      if ((refuseGroup && data.cmd === 'engineer_update_settings') || (refuseAgent && data.cmd === 'update_agent_digest_settings')) await route.fulfill({ json: { ok: false, error: 'Injected notification preset refusal' } }); else await route.continue();
    });
    await page.goto('/'); await page.getByText('Engineer behavior defaults', { exact: true }).click();
    const defaults = page.locator('details').filter({ has: page.getByText('Engineer behavior defaults', { exact: true }) }); const preset = defaults.getByRole('combobox', { name: 'Engineer notification preset' });
    await expect(preset).toHaveValue('normal'); await preset.selectOption('quiet'); await expect(defaults.getByRole('combobox', { name: 'Heartbeat interval', exact: true })).toHaveValue('0');
    const events = defaults.getByRole('group', { name: 'Enabled events', exact: true }); await events.getByRole('checkbox', { name: 'Task health alert' }).uncheck(); await events.getByLabel('Additional Engineer event name', { exact: true }).fill('qa_custom_event'); await events.getByRole('button', { name: 'Add Engineer event' }).click(); await expect(preset).toHaveValue('custom');
    const before = connections; await socket!.close({ code: 1012, reason: 'Notification custom draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(events.getByRole('checkbox', { name: 'Task derived' })).toBeChecked(); await expect(events.getByRole('checkbox', { name: 'Qa custom event' })).toBeChecked(); await expect(preset).toHaveValue('custom');
    await preset.selectOption('quiet'); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected notification preset refusal'); await expect(preset).toHaveValue('quiet');
    expect((await command(request, { cmd: 'get_group_settings', group })).engineer_settings).toMatchObject(normal);
    refuseGroup = false; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect((await command(request, { cmd: 'get_group_settings', group })).engineer_settings).toMatchObject({ ...quiet, custom_instructions: 'Keep group instructions' });
    await preset.scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('notification-group.png') });
    await page.reload(); await page.getByText('Engineer behavior defaults', { exact: true }).click(); await expect(preset).toHaveValue('quiet');
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Engineer…' }).click();
    const create = page.getByRole('dialog', { name: 'New engineer' }); const creationPreset = create.getByRole('combobox', { name: 'Engineer notification preset' }); await expect(creationPreset).toHaveValue('quiet'); await creationPreset.selectOption('noisy');
    await create.getByRole('textbox', { name: 'Enabled digest events', exact: true }).fill(''); await expect(creationPreset).toHaveValue('custom'); await create.getByLabel('Name', { exact: true }).fill('Notification QA engineer'); await create.getByLabel('Provider', { exact: true }).fill('generic'); await create.getByLabel('Boot command', { exact: true }).fill('/bin/cat');
    await create.getByRole('button', { name: 'Create engineer', exact: true }).click(); await expect(create).toHaveCount(0); expect(agent).not.toBe('');
    const resolved = async () => (await command(request, { cmd: 'get_agent_settings', agent_id: agent })).resolved as Row;
    expect(await resolved()).toMatchObject({ heartbeat_interval: { value: 60 }, enabled_events: { value: [] }, digest_verbosity: { value: 'detailed' } }); expect(writes.find((row) => row.cmd === 'add_engineer')).toMatchObject({ agent_digest_settings: { ...noisy, enabled_events: [] } });
    await page.getByRole('button', { name: 'Settings', exact: true }).click(); const settings = page.getByRole('dialog', { name: 'Agent settings', exact: true }); const agentPreset = settings.getByRole('combobox', { name: 'Engineer notification preset' }); await expect(agentPreset).toHaveValue('custom'); await agentPreset.selectOption('normal');
    await settings.getByRole('button', { name: 'Save settings', exact: true }).click(); await expect(settings.getByRole('alert')).toContainText('Injected notification preset refusal'); await expect(agentPreset).toHaveValue('normal');
    refuseAgent = false; await settings.getByRole('checkbox', { name: 'Relaunch after saving launch-bound changes' }).check(); await settings.getByRole('button', { name: 'Save settings', exact: true }).click(); await expect(settings).toHaveCount(0);
    const final = await resolved(); for (const [key, value] of Object.entries(normal)) expect(final[key]).toMatchObject({ value });
    expect(writes.slice(-2).map((row) => row.cmd)).toEqual(['update_agent_digest_settings', 'relaunch_agent']);
    await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${agent}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(agentPreset).toHaveValue('normal');
    await agentPreset.scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('notification-agent.png') });
  } finally { if (agent) await command(request, { cmd: 'remove_agent', id: agent }); }
});
