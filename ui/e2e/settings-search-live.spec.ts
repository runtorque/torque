import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('settings search reveals scopes, preserves drafts and caret, and refreshes results after reconnect', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Search settings ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', env_vars: { SEARCH_OLD: 'old' } } });
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
  const writes: Row[] = [];
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data); await route.continue(); });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const search = page.getByRole('searchbox', { name: 'Search settings' });
  const directory = page.getByRole('textbox', { name: 'Default directory', exact: true });
  await directory.fill('/private/tmp/search-local-draft');
  await directory.evaluate((input: HTMLInputElement) => { input.dataset.searchAnchor = 'original'; input.setSelectionRange(2, 10); });
  const secret = page.getByLabel('Anthropic key', { exact: true }); await secret.fill('fixture-secret-unsearchable');
  for (const [query, result, label] of [
    ['age expiry', /^Event ingest max days — .*Global defaults$/, 'Event ingest max days'],
    ['engineer worker concurrency', /^Default worker concurrency — .*Engineer behavior defaults$/, 'Default worker concurrency'],
    ['architect heartbeat interval', /^Architect heartbeat interval — .*Architect behavior defaults$/, 'Architect heartbeat interval'],
    ['shell execution', /^Shell — /, 'Shell'],
    ['embedding model', /^Embedding model — AI subsystem$/, 'Embedding model'],
    ['contrast appearance', /^Contrast — Appearance$/, 'Contrast'],
    ['blue accent', /^blue accent — Appearance$/, 'blue accent'],
    ['open board shortcut', /^Open Board shortcut — Keyboard shortcuts$/, 'Open Board shortcut'],
  ] as const) {
    await search.fill(query); const choice = page.getByRole('button', { name: result }); await expect(choice).toBeVisible(); await choice.click();
    const target = page.getByRole('combobox', { name: label, exact: true }).or(page.getByRole('textbox', { name: label, exact: true })).or(page.getByRole('spinbutton', { name: label, exact: true })).or(page.getByRole('button', { name: label, exact: true })); await expect(target).toBeFocused(); await expect(target).toBeInViewport(); await expect(search).toHaveValue('');
    await expect(directory).toHaveValue('/private/tmp/search-local-draft'); await expect(directory).toHaveAttribute('data-search-anchor', 'original');
  }
  expect(await directory.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([2, 10]);
  for (const value of ['fixture-secret-unsearchable', '/private/tmp/search-local-draft', 'no-such-setting']) {
    await search.fill(value); await expect(page.getByRole('status')).toContainText('No settings found'); await search.press('Enter'); expect(writes).toHaveLength(0);
  }
  await search.fill('env vars search'); await expect(page.getByRole('button', { name: /^Env vars: SEARCH_OLD — / })).toBeVisible();
  await command(request, { cmd: 'update_group_settings', group, settings: { env_vars: { SEARCH_NEW: 'fresh' } } });
  await search.evaluate((input: HTMLInputElement) => input.setSelectionRange(2, 7));
  const before = connections; await socket!.close({ code: 1012, reason: 'Search refresh acceptance' }); await expect.poll(() => connections).toBeGreaterThan(before);
  await expect(page.getByRole('button', { name: /^Env vars: SEARCH_NEW — / })).toBeVisible(); await expect(page.getByRole('button', { name: /^Env vars: SEARCH_OLD — / })).toHaveCount(0);
  await expect(search).toBeFocused(); await expect(search).toHaveValue('env vars search');
  expect(await search.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([2, 7]);
  await search.press('ArrowDown'); await expect(page.getByRole('button', { name: /^Env vars: SEARCH_NEW — / })).toBeFocused(); await page.keyboard.press('Enter');
  await expect(page.getByLabel('Env vars: SEARCH_NEW', { exact: true })).toBeFocused();
  await search.fill('heartbeat'); await search.evaluate((input) => input.scrollIntoView({ block: 'center' })); await page.screenshot({ path: test.info().outputPath('settings-search-results.png') });
  await page.setViewportSize({ width: 390, height: 780 }); await search.evaluate((input) => input.scrollIntoView({ block: 'center' }));
  const bounds = await search.boundingBox(); expect(bounds).not.toBeNull(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: test.info().outputPath('settings-search-narrow.png') });
  await search.press('Escape'); await expect(search).toHaveValue(''); await expect(search).toBeFocused();
  await search.fill('heartbeat'); await page.getByRole('button', { name: 'Clear settings search' }).click(); await expect(search).toBeFocused();
  await expect(directory).toHaveValue('/private/tmp/search-local-draft'); await expect(secret).toHaveValue('fixture-secret-unsearchable'); expect(writes).toHaveLength(0);
});
