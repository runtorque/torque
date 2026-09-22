import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
const preference = (activePanel: string, controlTab = 'mission') => ({ version: 1, activePanel, controlTab });
test('workspace preferences survive reload, serialize saves, retain local navigation and isolate detached windows', async ({ page, request, context }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Navigation ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: preference('board') });
  const requests: Row[] = []; const saved: Row[] = []; let refuse = false; let hold = false; let release: (() => void) | undefined;
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'ui_set_react_workspace_state') { await route.continue(); return; }
    requests.push(data);
    if (refuse) { await route.fulfill({ json: { ok: false, error: 'Injected preference refusal' } }); return; }
    if (hold) { hold = false; await new Promise<void>((resolve) => { release = resolve; }); }
    const response = await route.fetch(); const result = await response.json() as { data: Row }; saved.push(result.data.state as Row); await route.fulfill({ response });
  });
  try {
    await page.goto('/'); await expect(page.getByRole('heading', { name: 'Board', exact: true })).toBeVisible();
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Context', exact: true }).click();
    await expect.poll(() => saved.at(-1)).toEqual(preference('control', 'context'));
    await page.reload(); await expect(page.getByRole('heading', { name: 'Control Center', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: 'Context', exact: true })).toHaveAttribute('aria-current', 'page');
    const before = requests.length;
    const other = await context.newPage(); await other.goto('/'); await expect(other.getByRole('button', { name: 'Context', exact: true })).toHaveAttribute('aria-current', 'page');
    const otherSave = other.waitForResponse((response) => { if (!response.url().endsWith('/api/cmd')) return false; const data = response.request().postDataJSON() as Row; return data.cmd === 'ui_set_react_workspace_state' && (data.state as Row).activePanel === 'planning'; });
    await other.getByRole('button', { name: /◇ Planning/ }).click(); await expect(other.getByRole('heading', { name: 'Planning', exact: true })).toBeVisible();
    expect(await (await otherSave).json()).toMatchObject({ ok: true, data: { type: 'react_workspace_state', state: preference('planning', 'context') } });
    await expect(page.getByRole('heading', { name: 'Control Center', exact: true })).toBeVisible();
    const connected = connections; await socket!.close({ code: 1012, reason: 'Navigation reconnect' }); await expect.poll(() => connections).toBeGreaterThan(connected);
    await expect(page.getByRole('button', { name: 'Context', exact: true })).toHaveAttribute('aria-current', 'page'); expect(requests).toHaveLength(before);
    await other.close();
    refuse = true; await page.getByRole('button', { name: 'Help', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected preference refusal'); await expect(page.getByRole('button', { name: 'Help', exact: true })).toHaveAttribute('aria-current', 'page');
    refuse = false; await page.getByRole('button', { name: 'Retry workspace save', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0); await expect.poll(() => saved.at(-1)).toEqual(preference('control', 'help'));
    hold = true; await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect.poll(() => Boolean(release)).toBe(true); const heldCount = requests.length;
    await page.getByRole('button', { name: /◇ Planning/ }).click(); await expect(page.getByRole('heading', { name: 'Planning', exact: true })).toBeVisible(); expect(requests).toHaveLength(heldCount);
    release!(); release = undefined; await expect.poll(() => saved.at(-1)).toEqual(preference('planning', 'help'));
    await page.reload(); await expect(page.getByRole('heading', { name: 'Planning', exact: true })).toBeVisible();
    const detached = await context.newPage(); const detachedSaves: Row[] = [];
    await detached.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'ui_set_react_workspace_state') detachedSaves.push(data); await route.continue(); });
    await detached.goto('/?panel=control&window=navigation-qa'); await expect(detached.getByRole('heading', { name: 'Control Center', exact: true })).toBeVisible(); await detached.getByRole('button', { name: 'Logs', exact: true }).click();
    await command(request, { cmd: 'ui_set_react_workspace_state', state: preference('agents', 'context') });
    await expect(detached.getByRole('button', { name: 'Logs', exact: true })).toHaveAttribute('aria-current', 'page'); expect(detachedSaves).toHaveLength(0); await detached.close();
    await expect(page.getByRole('heading', { name: 'Planning', exact: true })).toBeVisible(); await page.reload(); await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
    await page.getByRole('button', { name: /◎ Control/ }).click(); await expect(page.getByRole('button', { name: 'Context', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect.poll(() => saved.at(-1)).toEqual(preference('control', 'context'));
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('restored-workspace.png') });
  } finally { release?.(); }
});
