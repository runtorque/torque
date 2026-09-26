import { expect, test, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
for (const phase of ['request', 'body'] as const) test(`workspace ${phase} deadline preserves latest navigation through retry and late persistence`, async ({ page, request }) => {
  test.setTimeout(85_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const command = async (data: Row) => { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; data: Row }; expect(result.ok).toBe(true); return result.data; };
  const preference = (activePanel: string) => ({ version: 1, activePanel, controlTab: 'mission' });
  await command({ cmd: 'ui_set_react_workspace_state', state: preference('board') });
  const calls: Row[] = []; const replies: Row[] = []; const releases: (() => void)[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.addInitScript((phase) => {
    const original = window.fetch.bind(window); let count = 0;
    window.fetch = async (input, options) => {
      const command = typeof options?.body === 'string' ? JSON.parse(options.body) as { cmd?: string } : {};
      if (command.cmd !== 'ui_set_react_workspace_state') return original(input, options);
      const index = count++; const response = await original(input, { ...options, signal: null });
      if (phase === 'body' && index === 0) {
        const parse = response.json.bind(response);
        response.json = () => new Promise((resolve, reject) => {
          (window as Window & { releaseNavigationBody?: () => Promise<void> }).releaseNavigationBody = async () => { try { resolve(await parse()); } catch (error) { reject(error instanceof Error ? error : new Error('Body fixture failed')); } };
        });
      }
      return response;
    };
  }, phase);
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'ui_set_react_workspace_state') { await route.continue(); return; }
    const index = calls.length; calls.push(data);
    // Hold the original request before it reaches the daemon. Its newer intent
    // must commit first, otherwise a browser-only stale-response test is weaker.
    if (phase === 'request' && index === 0) await new Promise<void>((resolve) => { releases[index] = resolve; });
    const response = await route.fetch(); replies[index] = await response.json() as Row;
    if (index === 1) await new Promise<void>((resolve) => { releases[index] = resolve; });
    await route.fulfill({ response });
  });
  try {
    await page.goto('/'); await expect(page.getByRole('heading', { level: 1, name: 'Board', exact: true })).toBeVisible();
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect.poll(() => calls.length).toBe(1);
    const before = connections; await socket!.close({ code: 1012, reason: 'Navigation deadline recovery' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(page.getByRole('alert')).toContainText('Workspace save timed out', { timeout: 35_000 });
    await expect(page.getByRole('heading', { level: 1, name: 'Agents', exact: true })).toBeVisible(); expect(calls).toHaveLength(1);
    await page.setViewportSize({ width: 760, height: 720 }); await expect(page.getByRole('button', { name: 'Retry workspace save', exact: true })).toBeInViewport();
    await page.screenshot({ path: test.info().outputPath(`workspace-${phase}-timeout.png`), animations: 'disabled' });
    if (phase === 'body') {
      await page.getByRole('button', { name: 'Retry workspace save', exact: true }).click(); await expect.poll(() => Boolean(releases[1])).toBe(true);
      expect(calls[1]).toEqual(calls[0]);
    }
    await page.getByRole('button', { name: /◇ Planning/ }).click(); await expect(page.getByRole('heading', { level: 1, name: 'Planning', exact: true })).toBeVisible();
    await expect.poll(() => Boolean(releases[1])).toBe(true);
    if (phase === 'request') {
      expect(calls[1]!.writer_id).toBe(calls[0]!.writer_id); expect(calls[1]!.revision).toBeGreaterThan(calls[0]!.revision as number);
      expect(replies[1]).toMatchObject({ ok: true, data: { state: preference('planning') } });
      releases[0]!(); await expect.poll(() => replies[0]).toBeTruthy();
      expect(JSON.stringify(replies[0])).toContain('superseded');
    } else await page.evaluate(async () => { await (window as Window & { releaseNavigationBody?: () => Promise<void> }).releaseNavigationBody?.(); });
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(calls).toHaveLength(2); await expect(page.getByRole('alert')).toHaveCount(0);
    releases[1]!();
    if (phase === 'body') await expect.poll(() => replies[2]).toMatchObject({ ok: true, data: { state: preference('planning') } });
    else await expect.poll(() => replies[1]).toMatchObject({ ok: true, data: { state: preference('planning') } });
    const stored = await command({ cmd: 'get_state' }); expect(stored.react_workspace_state).toEqual(preference('planning'));
    await page.reload(); await expect(page.getByRole('heading', { level: 1, name: 'Planning', exact: true })).toBeVisible();
  } finally { releases.forEach((release) => release()); }
});
