import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Health displays live daemon telemetry, refreshes visible history and preserves reading state across retries and reconnect', async ({ page, request }) => {
  test.setTimeout(130_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Health ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'mission' } });
  let socket: WebSocketRoute | undefined; let connections = 0; let latest: Row | undefined; let freezeTicks = false;
  const reads: Row[] = []; const completed: Row[] = []; let refuse = false;
  await page.routeWebSocket(/\/ws\?/, (connection) => {
    socket = connection; connections++; const server = connection.connectToServer();
    server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'metrics_tick') { if (freezeTicks) return; latest = frame; }
      connection.send(message);
    });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (!['get_system_health_metrics', 'get_metrics_history'].includes(String(data.cmd))) { await route.continue(); return; }
    reads.push(data);
    if (refuse) await route.fulfill({ json: { ok: false, error: 'Injected Health refresh failure' } });
    else { const response = await route.fetch(); await route.fulfill({ response }); completed.push(data); }
  });
  await page.goto('/'); const health = page.getByRole('region', { name: 'Health history', exact: true });
  await expect(health.getByRole('img', { name: /Process memory history/ })).toBeVisible();
  await expect.poll(() => ((latest?.perf as Row | undefined)?.frontend as Row | undefined)?.render_per_s, { timeout: 12_000 }).not.toBeUndefined();
  freezeTicks = true;
  const frontend = (latest!.perf as Row).frontend as Row;
  const live = health.getByRole('article', { name: 'Frontend renders live' });
  await expect(live).toContainText(`${Number(frontend.render_per_s).toFixed(1)} /s`);
  await expect(live).toContainText(`${Number(frontend.render_ms_p95).toFixed(1)} ms`);
  await expect(health.getByRole('img', { name: /Frontend.*history/ })).toHaveCount(0);
  await live.scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('health-live-metrics.png') });
  freezeTicks = false;
  await health.getByLabel('Health scope').selectOption('all'); await health.getByLabel('Health history window').selectOption('7d');
  await expect.poll(() => completed.filter((data) => data.group === '' && data.window === '7d').length).toBe(2);
  await expect(health).toContainText('Workflow scope: all_groups');
  const chart = health.getByRole('img', { name: /Process memory history/ }).locator('..');
  await chart.getByText('Inspect samples', { exact: true }).click(); await expect(chart.locator('details')).toHaveAttribute('open', '');
  const scope = health.getByLabel('Health scope'); await scope.focus();
  const count = reads.length; const started = Date.now(); const settled = completed.length;
  // Exercise the real production minute timer; do not accelerate unrelated liveness timers.
  await expect.poll(() => reads.length, { timeout: 70_000, intervals: [1000] }).toBeGreaterThan(count);
  expect(Date.now() - started).toBeGreaterThan(55_000);
  await expect.poll(() => completed.length).toBeGreaterThan(settled);
  await expect(chart.locator('details')).toHaveAttribute('open', ''); await expect(scope).toBeFocused();
  await expect(health.getByLabel('Health history window')).toHaveValue('7d');
  refuse = true; await health.getByRole('button', { name: 'Refresh health' }).click();
  await expect(health.getByRole('alert')).toContainText('Injected Health refresh failure'); await expect(chart).toBeVisible();
  refuse = false; await health.getByRole('button', { name: 'Refresh health' }).click(); await expect(health.getByRole('alert')).toHaveCount(0);
  const beforeReconnect = reads.length; const connected = connections; await socket!.close({ code: 1012, reason: 'Health reconnect' });
  await expect.poll(() => connections).toBeGreaterThan(connected); await expect.poll(() => reads.length).toBeGreaterThan(beforeReconnect);
  await expect(chart.locator('details')).toHaveAttribute('open', ''); await expect(scope).toHaveValue('all');
  await page.getByRole('button', { name: 'Help', exact: true }).click(); await expect(health).toHaveCount(0);
  const hidden = reads.length; const oldConnections = connections; await socket!.close({ code: 1012, reason: 'Hidden Health reconnect' });
  await expect.poll(() => connections).toBeGreaterThan(oldConnections); await expect(page.getByLabel('Search documentation')).toBeVisible(); expect(reads).toHaveLength(hidden);
  await page.getByRole('button', { name: 'Mission Control', exact: true }).click();
  await expect(health.getByLabel('Health scope')).toHaveValue('all'); await expect(health.getByLabel('Health history window')).toHaveValue('7d');
  await expect(health).toContainText('Workflow scope: all_groups');
});
