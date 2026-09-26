import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('paused Supervisor and Logs refresh once after real reconnect while preserving reading state', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Reconnect ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'mission' } });
  await command(request, { cmd: 'ui_set_supervisor_panel_state', state: { autoRefresh: true } });
  let socket: WebSocketRoute | undefined; let connections = 0; let supervisorReads = 0; let logReads = 0; let hold = false; const releases: (() => void)[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { socket = connection; connections++; connection.connectToServer(); });
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/logs') logReads++;
    if (url.pathname === '/api/cmd' && (request.postDataJSON() as Row)?.cmd === 'supervisor_sessions_list') supervisorReads++;
  });
  await page.route('**/api/cmd', async (route) => { if (hold && (route.request().postDataJSON() as Row).cmd === 'supervisor_sessions_list') await new Promise<void>((resolve) => { releases.push(resolve); }); await route.continue().catch(() => {}); });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Paused operational refresh' }); await expect.poll(() => connections).toBeGreaterThan(before); };
  await page.goto('/');
  const row = page.getByRole('button', { name: /PTY supervisor/ }); await expect(row).toBeVisible();
  const auto = page.getByLabel('Auto-refresh sessions');
  const pause = page.waitForResponse((response) => response.url().endsWith('/api/cmd') && (response.request().postDataJSON() as Row)?.cmd === 'supervisor_sessions_list');
  await auto.uncheck(); await pause;
  await page.getByLabel('Sort sessions').selectOption('pid'); await row.click(); await expect(row).toHaveAttribute('aria-expanded', 'true'); await row.focus();
  hold = true; await reconnect(); await expect(page.getByRole('alert')).toContainText('Supervisor refresh timed out', { timeout: 20_000 }); await expect(row).toBeFocused(); await expect(row).toHaveAttribute('aria-expanded', 'true'); await expect(auto).not.toBeChecked(); hold = false; releases.forEach((release) => release()); await page.getByRole('button', { name: 'Refresh sessions', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0); await row.focus();
  const beforeSupervisor = supervisorReads; await reconnect(); await expect.poll(() => supervisorReads).toBe(beforeSupervisor + 1);
  await expect(auto).not.toBeChecked(); await expect(row).toHaveAttribute('aria-expanded', 'true'); await expect(row).toBeFocused(); await expect(page.getByLabel('Sort sessions')).toHaveValue('pid');
  await page.waitForTimeout(2500); expect(supervisorReads).toBe(beforeSupervisor + 1);
  // Populate an actual bounded daemon log with read-only requests for scroll acceptance.
  for (let index = 0; index < 50; index++) await command(request, { cmd: 'get_global_settings' });
  await page.getByRole('button', { name: 'Logs', exact: true }).click(); const logs = page.getByRole('log'); await expect(logs).toContainText('get_global_settings');
  const pausedLog = page.waitForResponse((response) => new URL(response.url()).pathname === '/logs'); await page.getByLabel('Follow', { exact: true }).uncheck(); await pausedLog;
  await page.getByRole('combobox', { name: 'Level', exact: true }).selectOption('INFO'); const search = page.getByLabel('Search logs', { exact: true }); await search.fill('CMD');
  await logs.evaluate((element) => { element.scrollTop = 31; }); await expect.poll(() => logs.evaluate((element) => element.scrollTop)).toBe(31); await search.focus();
  const beforeLogs = logReads; await reconnect(); await expect.poll(() => logReads).toBe(beforeLogs + 1);
  await expect(page.getByLabel('Follow', { exact: true })).not.toBeChecked(); await expect(search).toHaveValue('CMD'); await expect(search).toBeFocused(); await expect(page.getByRole('combobox', { name: 'Level', exact: true })).toHaveValue('INFO');
  await expect.poll(() => logs.evaluate((element) => element.scrollTop)).toBe(31); await page.waitForTimeout(2500); expect(logReads).toBe(beforeLogs + 1); expect(supervisorReads).toBe(beforeSupervisor + 1);
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('paused-logs-reconnected.png') });
  await page.getByRole('button', { name: 'Help', exact: true }).click(); await expect(page.getByLabel('Search documentation')).toBeVisible();
  const hiddenLogs = logReads; const hiddenSupervisor = supervisorReads; await reconnect(); await page.waitForTimeout(2500);
  expect(logReads).toBe(hiddenLogs); expect(supervisorReads).toBe(hiddenSupervisor);
});
