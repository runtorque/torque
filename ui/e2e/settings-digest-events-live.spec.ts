import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('named digest event controls preserve presets, mandatory floors and explicit empty lists through save and reconnect', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Digest event controls ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'engineer_update_settings', group, enabled_events: ['agent_started', 'extension_event'], custom_instructions: 'Keep instructions' });
  await command(request, { cmd: 'update_architect_settings', group, settings: { architect_enabled_events: ['task_done'] } });
  let socket: WebSocketRoute | undefined; let connections = 0; let refuse = false; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (['engineer_update_settings', 'update_architect_settings'].includes(String(data.cmd))) { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA event save refused' } }); return; } } await route.continue(); });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText('Engineer behavior defaults', { exact: true }).click(); await page.getByText('Architect behavior defaults', { exact: true }).click(); };
  const engineer = page.getByRole('group', { name: 'Enabled events', exact: true }); const architect = page.getByRole('group', { name: 'Architect enabled events', exact: true });
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  await page.goto('/'); await open();
  await expect(engineer.getByRole('list', { name: 'Events always included in Engineer digests' })).toContainText('Worker boot failed'); await expect(architect.getByRole('list', { name: 'Events always included in Architect digests' })).toContainText('Task blocked');
  await expect(engineer.getByRole('checkbox', { name: 'Agent error' })).toHaveCount(0); await expect(architect.getByRole('checkbox', { name: 'Task blocked' })).toHaveCount(0);
  await engineer.getByRole('checkbox', { name: 'Task dispatched' }).check(); await architect.getByRole('checkbox', { name: 'Engineer queue empty' }).check();
  const focus = engineer.getByRole('checkbox', { name: 'Task dispatched' }); await focus.focus(); await focus.evaluate((input) => input.setAttribute('data-retained', 'yes')); expect(writes).toHaveLength(0);
  const before = connections; await socket!.close({ code: 1012, reason: 'Digest event draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
  await expect(focus).toBeFocused(); await expect(focus).toHaveAttribute('data-retained', 'yes'); await expect(focus).toBeChecked(); await expect(engineer.getByRole('checkbox', { name: 'Extension event' })).toBeChecked(); await expect(architect.getByRole('checkbox', { name: 'Engineer queue empty' })).toBeChecked();
  refuse = true; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA event save refused'); await expect(focus).toBeChecked(); await save();
  let frame = await command(request, { cmd: 'get_group_settings', group }); expect(frame.engineer_settings).toMatchObject({ enabled_events: ['agent_started', 'extension_event', 'task_dispatched'], custom_instructions: 'Keep instructions' }); expect(frame.architect_settings).toMatchObject({ architect_enabled_events: ['task_done', 'engineer_queue_empty'] });
  await page.reload(); await open(); await expect(focus).toBeChecked(); await expect(engineer.getByRole('checkbox', { name: 'Extension event' })).toBeChecked();
  for (const box of await engineer.getByRole('checkbox').all()) await box.uncheck(); for (const box of await architect.getByRole('checkbox').all()) await box.uncheck(); await save();
  frame = await command(request, { cmd: 'get_group_settings', group }); expect((frame.engineer_settings as Row).enabled_events).toEqual([]); expect((frame.architect_settings as Row).architect_enabled_events).toEqual([]);
  const preset = page.getByRole('combobox', { name: 'Engineer notification preset', exact: true }); await preset.selectOption('quiet'); await expect(engineer.getByRole('checkbox', { name: 'Task derived' })).toBeChecked(); await expect(engineer.getByRole('checkbox', { name: 'Agent started' })).not.toBeChecked();
  await engineer.getByRole('checkbox', { name: 'Agent started' }).check(); await expect(preset).toHaveValue('custom'); await page.getByRole('button', { name: 'Reset Enabled events', exact: true }).click(); await expect(engineer.getByRole('checkbox', { name: 'Task dispatched' })).toBeChecked();
  await engineer.evaluate((element) => element.scrollIntoView({ block: 'center' })); await page.screenshot({ path: test.info().outputPath('engineer-event-controls.png') });
  await architect.getByRole('checkbox', { name: 'Workflow breach' }).check(); await architect.evaluate((element) => element.scrollIntoView({ block: 'center' })); await page.screenshot({ path: test.info().outputPath('architect-event-controls.png') });
  await page.setViewportSize({ width: 390, height: 780 }); await architect.evaluate((element) => element.scrollIntoView({ block: 'center' }));
  const bounds = await architect.boundingBox(); expect(bounds).not.toBeNull(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: test.info().outputPath('architect-event-controls-narrow.png') });
});
