import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
async function prepare(request: APIRequestContext, suffix: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Creation recovery ${suffix} ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } });
  await command(request, { cmd: 'ui_select_group', group });
  return group;
}
let created: string[] = [];
test.afterEach(async ({ request }) => { for (const id of [...new Set(created)].reverse()) await command(request, { cmd: 'remove_agent', id }); created = []; });
test('Timed-out creation freezes the reviewed request and recovers one real terminal after reconnect', async ({ page, request }) => {
  test.setTimeout(90_000); await prepare(request, 'timeout');
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  const writes: Row[] = []; let release: (() => void) | undefined;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_terminal') { await route.continue(); return; }
    writes.push(data); const result = await route.fetch(); const body = await result.json() as { ok: boolean; data: Row };
    expect(body.ok).toBe(true); created.push(String(body.data.id));
    if (writes.length === 1) await new Promise<void>((resolve) => { release = resolve; });
    await route.fulfill({ response: result });
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Terminal…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New terminal' });
    await dialog.getByLabel('Name', { exact: true }).fill('Reviewed terminal'); await dialog.getByLabel('Boot command').fill('/bin/cat');
    await dialog.getByRole('button', { name: 'Create terminal', exact: true }).click();
    await expect.poll(() => Boolean(release)).toBe(true);
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 35_000 });
    await expect(dialog.getByLabel('Name', { exact: true })).toBeDisabled();
    await page.keyboard.press('ControlOrMeta+k'); await page.getByRole('combobox', { name: 'Search commands' }).fill('Open Board'); await page.getByRole('option', { name: 'Open Board', exact: true }).click();
    const guard = page.getByRole('dialog', { name: 'Creation needs recovery' }); await expect(guard).toBeVisible();
    await expect(guard).toContainText('Return to creation'); await guard.getByRole('button', { name: 'Return to creation' }).click();
    await expect(dialog).toBeVisible(); await expect(dialog.getByLabel('Name', { exact: true })).toHaveValue('Reviewed terminal');
    await dialog.getByRole('button', { name: 'Close dialog' }).click(); await page.keyboard.press('Escape'); await expect(dialog).toBeVisible();
    const before = connections; await socket!.close({ code: 1012, reason: 'Uncertain creation recovery' }); await expect.poll(() => connections).toBeGreaterThan(before); expect(writes).toHaveLength(1);
    await expect(dialog.getByLabel('Boot command')).toHaveValue('/bin/cat');
    await page.setViewportSize({ width: 760, height: 650 }); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('uncertain-creation.png') });
    release!(); await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Retry same creation' }).click(); await expect(dialog).toHaveCount(0);
    expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]); expect(new Set(created).size).toBe(1);
    await expect(page.locator(`[role="treeitem"][data-agent-id="${created[0]}"]`)).toHaveAttribute('aria-selected', 'true');
    await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect(page.locator('[role="treeitem"]').filter({ hasText: 'Reviewed terminal' })).toHaveCount(1);
  } finally { release?.(); }
});
test('A real post-allocation terminal failure identifies the target and permits deliberate inspection', async ({ page, request }) => {
  test.setTimeout(60_000); await prepare(request, 'partial');
  const writes: Row[] = []; let outcome: Row | undefined;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_terminal') { await route.continue(); return; }
    writes.push(data); const result = await route.fetch(); const body = await result.json() as { ok: boolean; data: Row };
    expect(body.ok).toBe(true); outcome = body.data; created.push(String((outcome.target as Row).id)); await route.fulfill({ response: result });
  });
  await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Terminal…' }).click();
  const dialog = page.getByRole('dialog', { name: 'New terminal' }); await dialog.getByLabel('Name', { exact: true }).fill('Incomplete terminal');
  // Terminal startup validation occurs after state allocation. No process is launched.
  await dialog.getByLabel('Boot command').fill('"unterminated'); await dialog.getByRole('button', { name: 'Create terminal', exact: true }).click();
  await expect(dialog.getByRole('region', { name: 'Incomplete launch' })).toContainText('The target exists, but launch did not finish.');
  expect(outcome).toMatchObject({ type: 'creation_incomplete', command: 'add_terminal', target: { type: 'agent', kind: 'terminal', name: 'Incomplete terminal' } });
  await expect(dialog.getByRole('region', { name: 'Incomplete launch' })).toContainText(created[0]!); await expect(dialog.getByLabel('Name', { exact: true })).toBeDisabled();
  expect(await command(request, writes[0]!)).toEqual(outcome);
  await page.setViewportSize({ width: 760, height: 650 }); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('partial-creation.png') });
  await dialog.getByRole('button', { name: 'Inspect created target' }).click(); await expect(dialog).toHaveCount(0); expect(writes).toHaveLength(1);
  await expect(page.locator(`[role="treeitem"][data-agent-id="${created[0]}"]`)).toHaveAttribute('aria-selected', 'true');
});
