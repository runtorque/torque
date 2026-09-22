import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Context reconnect preserves edits and filters, retries failed writes and persists acknowledged publish/edit/pin', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Context QA ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const initial = (await command(request, { cmd: 'memory_publish', scope_kind: 'group', scope_ref: group, entry_type: 'warning', title: 'Original context title', content: 'Original server content', source_kind: 'manual' })).entry as Row;
  let socket: WebSocketRoute | undefined; let connections = 0; let refusal = ''; const commands: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Context reconnect acceptance' }); await expect.poll(() => connections).toBeGreaterThan(before); };
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (String(data.cmd).startsWith('memory_')) commands.push(data);
    if (data.cmd === refusal) await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Injected context refusal' } } });
    else await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Context', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Original context title' })).toBeVisible(); await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const content = page.getByRole('textbox', { name: 'Content', exact: true }); await expect(content).toHaveAttribute('maxlength', '4000'); await expect(page.getByRole('textbox', { name: 'Title', exact: true })).toHaveAttribute('maxlength', '200'); await content.fill('Local draft survives reconnect');
  await page.getByRole('textbox', { name: 'Search', exact: true }).fill('unapplied search');
  await command(request, { cmd: 'memory_publish', entry_id: initial.id, title: 'Remote context title', content: 'Remote content', pinned: true });
  await content.focus(); await content.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(3, 9); node.dataset.contextAnchor = 'original'; });
  const before = commands.length; await reconnect();
  await expect(page.getByRole('textbox', { name: 'Title', exact: true })).toHaveValue('Remote context title');
  await expect(content).toHaveValue('Local draft survives reconnect'); await expect(content).toBeFocused(); await expect(content).toHaveAttribute('data-context-anchor', 'original'); expect(await content.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([3, 9]);
  expect(commands.slice(before)).toEqual([{ cmd: 'memory_list', group_name: group, search: '', entry_type: '', pinned_only: false, limit: 100 }]);
  refusal = 'memory_list'; await reconnect(); await expect(page.getByRole('alert')).toContainText('Context refresh failed'); await expect(content).toHaveValue('Local draft survives reconnect');
  refusal = ''; await page.getByRole('button', { name: 'Retry context', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0);
  refusal = 'memory_publish'; await page.getByRole('button', { name: 'Save context', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected context refusal'); await expect(content).toHaveValue('Local draft survives reconnect');
  refusal = ''; await page.getByRole('button', { name: 'Save context', exact: true }).click(); await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible();
  expect(commands.filter((item) => item.cmd === 'memory_publish')).toEqual([{ cmd: 'memory_publish', content: 'Local draft survives reconnect', entry_id: initial.id }, { cmd: 'memory_publish', content: 'Local draft survives reconnect', entry_id: initial.id }]);
  expect((await command(request, { cmd: 'memory_read', entry_id: initial.id })).entry).toMatchObject({ title: 'Remote context title', content: 'Local draft survives reconnect', pinned: true, source_kind: 'manual', expires_at: initial.expires_at });
  refusal = 'memory_unpin'; await page.getByRole('button', { name: 'Unpin', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected context refusal'); await expect(page.getByRole('button', { name: 'Unpin', exact: true })).toBeEnabled();
  refusal = ''; await page.getByRole('button', { name: 'Unpin', exact: true }).click(); await expect(page.getByRole('button', { name: 'Pin', exact: true })).toBeVisible();
  expect((await command(request, { cmd: 'memory_read', entry_id: initial.id })).entry).toMatchObject({ pinned: false });
  await page.getByRole('button', { name: '＋ Add context', exact: true }).click(); await page.getByRole('textbox', { name: 'Title', exact: true }).fill('New durable context'); await content.fill('Created through acknowledged publish');
  refusal = 'memory_publish'; await page.getByRole('button', { name: 'Publish context', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected context refusal');
  refusal = 'memory_list'; await page.getByRole('button', { name: 'Publish context', exact: true }).click(); await expect(page.getByRole('heading', { name: 'New durable context' })).toBeVisible(); await expect(page.getByRole('alert')).toContainText('Context refresh failed');
  const publications = commands.filter((item) => item.cmd === 'memory_publish').length;
  refusal = ''; await page.getByRole('button', { name: 'Retry context', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0); expect(commands.filter((item) => item.cmd === 'memory_publish')).toHaveLength(publications);
  const all = (await command(request, { cmd: 'memory_list', group_name: group })).entries as Row[]; expect(all.filter((entry) => entry.title === 'New durable context')).toHaveLength(1);
  await page.getByRole('textbox', { name: 'Search', exact: true }).fill('New durable'); await page.getByRole('button', { name: 'Apply', exact: true }).click(); await expect(page.getByRole('complementary', { name: 'Shared context entries' }).getByRole('button')).toHaveCount(1);
  for (const entryType of ['finding', 'decision', 'warning', 'handoff', 'note']) {
    await page.getByRole('button', { name: '＋ Add context', exact: true }).click();
    const editor = page.locator('form').filter({ has: content });
    await expect(editor.getByRole('combobox', { name: 'Type', exact: true }).locator('option')).toHaveText(['finding', 'decision', 'warning', 'handoff', 'note']);
    await editor.getByRole('combobox', { name: 'Type', exact: true }).selectOption(entryType); await content.fill(`Supported ${entryType} entry`); await editor.getByRole('textbox', { name: 'Title', exact: true }).fill(`Type ${entryType}`);
    await page.getByRole('button', { name: 'Publish context', exact: true }).click(); await expect(content).toHaveCount(0);
  }
  const typed = (await command(request, { cmd: 'memory_list', group_name: group })).entries as Row[];
  for (const entryType of ['finding', 'decision', 'warning', 'handoff', 'note']) expect(typed.find((entry) => entry.title === `Type ${entryType}`)).toMatchObject({ entry_type: entryType, expires_at: expect.any(Number) });
  await page.getByRole('textbox', { name: 'Search', exact: true }).fill(''); await page.getByRole('button', { name: 'Apply', exact: true }).click();
  await expect(page.getByRole('complementary', { name: 'Shared context entries' }).getByRole('button')).toHaveCount(7);
  await expect(page.getByText('manual', { exact: true })).toBeVisible();
  await page.locator('header').filter({ has: page.getByRole('textbox', { name: 'Search', exact: true }) }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('context-acknowledged.png'), fullPage: true });
  await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); const hidden = commands.length; await reconnect(); expect(commands).toHaveLength(hidden);
});
