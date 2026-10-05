import { mkdir, rm } from 'node:fs/promises';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('pipeline reconnect retains the zoomed graph and node focus through a held response, failure and retry', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const stamp = Date.now(); const group = `Pipeline continuity ${stamp}`; const directory = `/private/tmp/torque-pipeline-continuity-${stamp}`;
  await mkdir(directory, { recursive: true }); await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory } }); await command(request, { cmd: 'ui_select_group', group });
  const saveBuild = (when: string) => command(request, { cmd: 'save_action', group, scope: 'project', name: 'continuity/build', action: { prompt: '{{ TASK }}', transitions: [{ action: 'continuity/review', when }] } });
  await saveBuild('Before reconnect');
  await command(request, { cmd: 'save_action', group, scope: 'project', name: 'continuity/review', action: { prompt: '{{ TASK }}', transitions: [{ action: 'continuity/build', when: 'Changes needed' }, { ask: true, when: 'Approval required' }] } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'pipelines' } });
  let socket: WebSocketRoute | undefined; let connections = 0; let reads = 0; let completed = 0; let hold = false; let refuse = false; let release: (() => void) | undefined;
  await page.routeWebSocket(/\/ws\?/, (connection) => { socket = connection; connections++; connection.connectToServer(); });
  await page.route('**/api/cmd', async (route) => {
    if ((route.request().postDataJSON() as Row).cmd !== 'discover_pipelines') { await route.continue(); return; }
    reads++;
    if (hold) { hold = false; await new Promise<void>((resolve) => { release = resolve; }); }
    if (refuse) await route.fulfill({ json: { ok: false, error: 'Injected pipeline refusal' } });
    else { const response = await route.fetch(); await route.fulfill({ response }).catch(() => {}); }
    completed++;
  });
  try {
    await page.goto('/'); const picker = page.getByRole('combobox', { name: 'Pipeline', exact: true }); await picker.selectOption('continuity/build');
    const graph = page.getByRole('group', { name: 'Pipeline graph' }); const original = await graph.elementHandle();
    await page.getByRole('button', { name: 'Zoom in pipeline' }).click(); await expect(page.getByText('125%', { exact: true })).toBeVisible();
    await graph.focus(); await page.keyboard.press('ArrowRight'); const viewBox = await graph.getAttribute('viewBox');
    const node = graph.getByRole('button', { name: 'Edit action continuity/review', exact: true }); await node.focus(); await expect(node).toHaveAttribute('aria-pressed', 'true');
    await saveBuild('After reconnect'); hold = true; const before = connections; await socket!.close({ code: 1012, reason: 'Pipeline continuity' });
    await expect.poll(() => connections).toBeGreaterThan(before); await expect.poll(() => Boolean(release)).toBe(true);
    await expect(graph).toBeVisible(); expect(await graph.evaluate((element, previous) => element === previous, original)).toBe(true); await expect(graph).toHaveAttribute('viewBox', viewBox!); await expect(node).toBeFocused(); await expect(picker).toHaveValue('continuity/build');
    await expect(page.getByRole('alert')).toContainText('refresh timed out', { timeout: 20_000 }); await expect(node).toBeFocused(); await expect(graph).toHaveAttribute('viewBox', viewBox!);
    release!(); release = undefined; await page.getByRole('button', { name: 'Discover pipelines', exact: true }).click(); await node.focus(); await expect(page.getByLabel('Pipeline transitions')).toContainText('After reconnect');
    await expect(node).toBeFocused(); await expect(node).toHaveAttribute('aria-pressed', 'true'); await expect(graph).toHaveAttribute('viewBox', viewBox!);
    refuse = true; await page.getByRole('button', { name: 'Discover pipelines', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected pipeline refusal');
    await expect(graph).toHaveAttribute('viewBox', viewBox!); refuse = false; const retry = completed;
    await page.getByRole('button', { name: 'Discover pipelines', exact: true }).click(); await node.focus(); await expect.poll(() => completed).toBeGreaterThan(retry); await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(node).toBeFocused(); expect(await graph.evaluate((element, previous) => element === previous, original)).toBe(true); await expect(graph).toHaveAttribute('viewBox', viewBox!);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('pipeline-reconnected.png') });
    await page.getByRole('button', { name: 'Help', exact: true }).click(); await expect(page.getByLabel('Search documentation')).toBeVisible();
    const hidden = reads; const connected = connections; await socket!.close({ code: 1012, reason: 'Hidden pipeline' }); await expect.poll(() => connections).toBeGreaterThan(connected);
    await expect(page.getByRole('button', { name: 'Torque README.md', exact: true })).toBeVisible(); expect(reads).toBe(hidden);
  } finally { release?.(); await rm(directory, { recursive: true, force: true }); }
});
