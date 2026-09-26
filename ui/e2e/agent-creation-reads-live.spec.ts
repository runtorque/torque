import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('creation read deadlines retain draft and class selection, retry and stop after closing', async ({ page, request }) => {
  test.setTimeout(75_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Creation reads ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic', agent_boot_command: '/bin/cat' } }); await command(request, { cmd: 'ui_select_group', group });
  let holdTemplate = true; let holdClass = false; let releaseTemplate = () => {}; let releaseClass = () => {}; let connections = 0; let socket: WebSocketRoute | undefined; const reads: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'render_template' || data.cmd === 'agent_class_list') reads.push(data);
    if (data.cmd === 'render_template' && holdTemplate) { holdTemplate = false; await new Promise<void>((resolve) => { releaseTemplate = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'template_rendered', group, name: '', config: { model: 'obsolete default' } } } }); return; }
    if (data.cmd === 'agent_class_list' && holdClass) { holdClass = false; await new Promise<void>((resolve) => { releaseClass = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'agent_classes', group, classes: [], issues: [] } } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New worker' }); const model = dialog.getByRole('textbox', { name: 'Model', exact: true }); const picker = dialog.getByRole('combobox', { name: 'Agent Class', exact: true }); const create = dialog.getByRole('button', { name: 'Create worker', exact: true });
    await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill('Retained creation draft'); await model.fill('explicit draft model'); await model.evaluate((node: HTMLInputElement) => { node.dataset.owner = 'retained'; node.setSelectionRange(2, 8); });
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(model).toBeFocused(); expect(await model.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]); await expect(create).toBeDisabled();
    await dialog.getByRole('button', { name: 'Retry launch settings' }).click(); await expect(create).toBeEnabled(); releaseTemplate(); await expect(model).toHaveValue('explicit draft model'); await expect(model).toHaveAttribute('data-owner', 'retained');
    const classId = await picker.locator('option:not([disabled])').evaluateAll((options) => (options as HTMLOptionElement[]).find((option) => option.value)?.value); expect(classId).toBeTruthy(); await picker.selectOption(classId!);
    holdClass = true; const before = connections; await socket!.close({ code: 1012, reason: 'Class read deadline' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(picker).toHaveValue(classId!); await expect(create).toBeDisabled(); await expect(model).toHaveValue('explicit draft model');
    await page.setViewportSize({ width: 760, height: 650 }); await dialog.getByRole('alert').scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('creation-class-timeout.png') });
    await dialog.getByRole('button', { name: 'Retry Agent Classes' }).click(); await expect(create).toBeEnabled(); releaseClass(); await expect(picker).toHaveValue(classId!);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const count = reads.length; const connected = connections; await socket!.close({ code: 1012, reason: 'Closed creation stays quiet' }); await expect.poll(() => connections).toBeGreaterThan(connected); await expect(page.getByText('connected', { exact: true })).toBeVisible(); expect(reads).toHaveLength(count);
  } finally { releaseTemplate(); releaseClass(); }
});
