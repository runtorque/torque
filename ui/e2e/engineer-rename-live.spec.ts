import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); return response.data;
}
let createdIds: string[] = [];
test.afterEach(async ({ request }) => { for (const id of createdIds) await command(request, { cmd: 'remove_agent', id }); createdIds = []; });
test('Engineer rename validates names, retains refused drafts and retries only unfinished identity work', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Rename ${Date.now()}`; const original = `${group} original`; const taken = `${group} taken`; const renamed = `${group} renamed`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
  const ids: string[] = []; createdIds = ids;
  for (const name of [original, taken]) ids.push(String((await command(request, { cmd: 'add_engineer', name, group, provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id));
  let socket: WebSocketRoute | undefined; let connections = 0; let refuseSettings = true; let releaseRename: (() => void) | undefined; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['rename_engineer', 'update_agent', 'update_agent_settings'].includes(String(data.cmd))) writes.push(data);
    if (data.cmd === 'rename_engineer' && data.new_name === renamed) {
      const response = await route.fetch(); await new Promise<void>((resolve) => { releaseRename = resolve; }); await route.fulfill({ response });
    } else if (refuseSettings && data.cmd === 'update_agent_settings') await route.fulfill({ json: { ok: false, error: 'Injected settings refusal' } });
    else await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${ids[0]}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Agent settings' }); const name = dialog.getByRole('textbox', { name: 'Name', exact: true }); const save = dialog.getByRole('button', { name: 'Save settings', exact: true });
  await name.fill('   '); await dialog.getByLabel('Icon', { exact: true }).fill('gear'); await save.click(); await expect(dialog.getByRole('alert')).toHaveText('Name is required.'); await expect(name).toBeFocused(); expect(writes).toHaveLength(0);
  await name.fill(taken); await save.click(); await expect(dialog.getByRole('alert')).toContainText(`Engineer '${taken}' already exists`); await expect(name).toHaveValue(taken); expect(writes).toEqual([{ cmd: 'rename_engineer', id: ids[0], new_name: taken }]);
  await name.focus(); await name.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 6); node.dataset.renameAnchor = 'original'; }); const before = connections; await socket!.close({ code: 1012, reason: 'Rename draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(name).toBeFocused(); await expect(name).toHaveAttribute('data-rename-anchor', 'original'); expect(await name.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 6]);
  await name.fill(`  ${renamed}  `); await dialog.getByLabel('Custom instructions', { exact: true }).fill('Retained settings draft'); await save.click(); await expect.poll(() => Boolean(releaseRename)).toBe(true); await expect(name).toBeDisabled(); await dialog.getByRole('button', { name: 'Close dialog' }).click(); await page.mouse.click(4, 4); await page.keyboard.press('Escape'); await expect(dialog).toBeVisible(); expect(writes).toHaveLength(2);
  releaseRename!(); await expect(dialog.getByRole('alert')).toHaveText('Identity changes were saved. Injected settings refusal'); await expect(name).toHaveValue(`  ${renamed}  `); expect(writes.map((data) => data.cmd)).toEqual(['rename_engineer', 'rename_engineer', 'update_agent', 'update_agent_settings']); expect(writes[2]).toEqual({ cmd: 'update_agent', id: ids[0], icon: 'gear' });
  expect((await command(request, { cmd: 'get_agent_history_detail', agent_id: ids[0] })).record).toMatchObject({ name: renamed });
  await expect(dialog.getByRole('alert')).toBeFocused(); await expect(dialog.getByRole('alert')).toBeInViewport(); await page.screenshot({ path: test.info().outputPath('engineer-rename-retained.png') });
  // A second operator changes the already-saved identity before the retry.
  const external = `${group} external`; await command(request, { cmd: 'rename_engineer', id: ids[0], new_name: external }); await command(request, { cmd: 'update_agent', id: ids[0], icon: 'external' });
  refuseSettings = false; await save.click(); await expect(dialog).toHaveCount(0); expect(writes.map((data) => data.cmd)).toEqual(['rename_engineer', 'rename_engineer', 'update_agent', 'update_agent_settings', 'update_agent_settings']);
  await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.locator(`[role="treeitem"][data-agent-id="${ids[0]}"]`).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(name).toHaveValue(external); await expect(dialog.getByLabel('Icon', { exact: true })).toHaveValue('external'); await expect(dialog.getByLabel('Custom instructions', { exact: true })).toHaveValue('Retained settings draft');
});
