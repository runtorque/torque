import { expect, test, type WebSocketRoute } from '@playwright/test';
import { compactStateFixture } from '../src/protocol/fixtures';
type Row = Record<string, unknown>;

test('production render telemetry includes independent child edits and expires to idle without acknowledgement feedback', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const reports: Row[] = []; const commands: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  const frame = { ...compactStateFixture, active_group: 'Foundation', react_workspace_state: { version: 1, activePanel: 'control', controlTab: 'help' } };
  await page.routeWebSocket(/\/ws\?/, (connection) => {
    socket = connection; connections++; connection.send(JSON.stringify(frame));
    connection.onMessage((message) => { commands.push(JSON.parse(String(message)) as Row); });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'report_frontend_render') { await route.continue(); return; }
    const response = await route.fetch(); expect(await response.json()).toMatchObject({ ok: true, data: { type: 'ok' } });
    reports.push(data); await route.fulfill({ response });
  });
  await page.goto('/'); await expect(page.getByLabel('Search documentation', { exact: true })).toBeVisible();
  // With no incoming state changes, reports themselves must not sustain nonzero render rates.
  await expect.poll(() => reports.at(-1), { timeout: 15_000 }).toMatchObject({ render_per_s: 0, render_ms_p95: 0 });
  const before = reports.length;
  const draft = page.getByLabel('Search documentation', { exact: true });
  await draft.pressSequentially('independent help draft', { delay: 30 });
  await expect.poll(() => reports.slice(before).some((item) => Number(item.render_per_s) > 0 && Number(item.render_ms_p95) > 0), { timeout: 6_000 }).toBe(true);
  await expect(draft).toHaveValue('independent help draft'); await expect(draft).toBeFocused();
  await expect.poll(() => reports.at(-1), { timeout: 10_000 }).toMatchObject({ render_per_s: 0, render_ms_p95: 0 });
  const idle = reports.length;
  await expect.poll(() => reports.length, { timeout: 6_000 }).toBeGreaterThan(idle);
  expect(reports.at(-1)).toMatchObject({ render_per_s: 0, render_ms_p95: 0 });
  expect(commands.some((command) => command.cmd === 'report_frontend_render')).toBe(false);
  const previousConnections = connections; await socket!.close({ code: 1012, reason: 'Telemetry reconnect' });
  await expect.poll(() => connections).toBeGreaterThan(previousConnections);
  await expect(draft).toHaveValue('independent help draft');
  const resumed = reports.length;
  await expect.poll(() => reports.length, { timeout: 6_000 }).toBeGreaterThan(resumed);
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('render-telemetry-help.png') });
});
