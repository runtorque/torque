import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function setting(page: Page) {
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('Context default ttl days');
  await page.getByRole('button', { name: /^Context default ttl days — / }).click();
  return page.getByLabel('Context default ttl days', { exact: true });
}
test('Saved Context lifetime controls new expiry while settings changes, editing and pinning preserve existing expiry', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Context expiry ${Date.now()}`; const entries: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { client.connectToServer(); socket = client; connections++; });
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.goto('/');
  for (const days of [1, 60, 7]) {
    const ttl = await setting(page); await ttl.fill(String(days)); await ttl.focus();
    const before = connections; await socket!.close({ code: 1012, reason: 'Context lifetime draft' });
    await expect.poll(() => connections).toBeGreaterThan(before); await expect(ttl).toBeFocused(); await expect(ttl).toHaveValue(String(days));
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    await page.reload(); await expect(await setting(page)).toHaveValue(String(days));
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ context_default_ttl_days: days });
    for (const previous of entries) expect((await command(request, { cmd: 'memory_read', entry_id: previous.id })).entry).toMatchObject({ expires_at: previous.expires_at });
    await page.getByRole('button', { name: 'Context', exact: true }).click();
    await page.getByRole('button', { name: '＋ Add context', exact: true }).click();
    const title = `Lifetime ${days} days`; await page.getByRole('textbox', { name: 'Title', exact: true }).fill(title);
    await page.getByRole('textbox', { name: 'Content', exact: true }).fill(`Published with ${days}-day lifetime`);
    await page.getByRole('button', { name: 'Publish context', exact: true }).click();
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
    const rows = (await command(request, { cmd: 'memory_list', group_name: group })).entries as Row[];
    const entry = rows.find((row) => row.title === title)!; expect(entry).toBeTruthy();
    expect(Number(entry.expires_at) - Number(entry.created_at)).toBe(days * 86400); entries.push(entry);
    const expiry = await page.evaluate((seconds) => new Date(seconds * 1000).toLocaleString(), Number(entry.expires_at));
    await expect(page.locator('dl > div').filter({ has: page.getByText('Expires', { exact: true }) })).toContainText(expiry);
    await page.getByRole('button', { name: 'Pin', exact: true }).click(); await expect(page.getByRole('button', { name: 'Unpin', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Edit', exact: true }).click(); await page.getByRole('textbox', { name: 'Content', exact: true }).fill('Edited without extending expiry');
    await page.getByRole('button', { name: 'Save context', exact: true }).click(); await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeVisible();
    expect((await command(request, { cmd: 'memory_read', entry_id: entry.id })).entry).toMatchObject({ expires_at: entry.expires_at, pinned: true, content: 'Edited without extending expiry' });
  }
  await page.screenshot({ path: test.info().outputPath('context-expiry.png'), animations: 'disabled' });
  const path = test.info().outputPath('context-expiry-evidence.json'); await writeFile(path, JSON.stringify(entries, null, 2)); await test.info().attach('context-expiry-evidence', { path, contentType: 'application/json' });
});
