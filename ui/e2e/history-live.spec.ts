import { execFileSync } from 'node:child_process';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('History reconnect refreshes persisted runs and detail, preserves reading state, retries and opens task links', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string; data_dir: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  // Fixture writes are limited to an explicitly isolated, local temporary profile.
  expect(runtime.data_dir).toMatch(/^\/(private\/)?tmp\//);
  const group = `History QA ${Date.now()}`; const id = `history-${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const task = String((await command(request, { cmd: 'board_add_task', group, task: 'History linked task' })).task_id);
  const seed = (refresh = false) => execFileSync('python3', ['-c', `
import sqlite3, sys, time
path, group, agent, task, refresh = sys.argv[1:]
with sqlite3.connect(path + '/torque.db') as db:
 if refresh == 'true':
  db.execute('UPDATE agent_history SET worktree_branch=?, total_tokens_in=? WHERE id=?', ('refreshed/history', 333, agent))
  db.execute('UPDATE agent_messages SET message=? WHERE agent_id=? AND action=?', ('Refreshed persistent message', agent, 'done'))
 else:
  for i in range(45):
   db.execute('INSERT INTO agent_history (id, name, "group", agent_type, template, created_at, removed_at, worktree_branch, total_tokens_in, total_tokens_out, total_tasks, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (agent if i == 0 else agent + '-' + str(i), 'History worker ' + str(i), group, 'generic', 'worker-role', time.time()-i, time.time(), 'original/history', 120, 45, 1, 'merged'))
  db.execute('INSERT INTO agent_tasks (agent_id, task_id, task_title, started_at, outcome) VALUES (?,?,?,?,?)', (agent, task, 'Stored task title', time.time(), 'done'))
  for i in range(60):
   db.execute('INSERT INTO agent_messages (agent_id, task_id, timestamp, action, message) VALUES (?,?,?,?,?)', (agent, task, time.time()-i, 'done' if i == 0 else 'progress', 'Original persistent message' if i == 0 else 'Recorded progress ' + str(i) + ': ' + 'History reading content. '*8))
`, runtime.data_dir, group, id, task, String(refresh)]);
  seed();
  let socket: WebSocketRoute | undefined; let connections = 0; let refusal = ''; const reads: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'History acceptance' }); await expect.poll(() => connections).toBeGreaterThan(before); };
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (String(data.cmd).startsWith('get_agent_history')) reads.push(data);
    if (data.cmd === refusal) await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Injected history refusal' } } }); else await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'History', exact: true }).click();
  const runs = page.getByRole('region', { name: 'Agent runs', exact: true }); const detail = page.getByRole('region', { name: 'Run detail', exact: true });
  await expect(runs.getByRole('button')).toHaveCount(45); await runs.getByRole('button', { name: /^History worker 0 / }).click();
  await expect(detail.getByText('Original persistent message', { exact: true })).toBeVisible(); await expect(detail.getByText('Stored task title', { exact: true })).toBeVisible(); await expect(detail.getByText('120 in / 45 out', { exact: true })).toBeVisible(); await expect(detail.getByText('worker-role', { exact: true })).toBeVisible();
  const disclosure = detail.locator('details').first(); await disclosure.locator('summary').click(); await expect(disclosure).toHaveAttribute('open', '');
  const search = page.getByRole('textbox', { name: 'Search history', exact: true }); await search.fill('History'); await search.evaluate((node: HTMLInputElement) => { node.setSelectionRange(1, 5); node.dataset.historyAnchor = 'original'; });
  await runs.evaluate((node) => { node.scrollTop = 200; }); await detail.evaluate((node) => { node.scrollTop = 500; });
  expect(await runs.evaluate((node) => node.scrollTop)).toBe(200); expect(await detail.evaluate((node) => node.scrollTop)).toBe(500);
  seed(true); const before = reads.length; await reconnect(); await expect(detail.getByText('Refreshed persistent message', { exact: true })).toHaveCount(1);
  await expect(disclosure).toHaveAttribute('open', '');
  expect(reads.slice(before)).toEqual([{ cmd: 'get_agent_history', status: 'merged', limit: 100 }, { cmd: 'get_agent_history_detail', agent_id: id, message_limit: 100 }]);
  await expect(search).toBeFocused(); await expect(search).toHaveAttribute('data-history-anchor', 'original'); await expect(search).toHaveValue('History'); expect(await search.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 5]);
  expect(await runs.evaluate((node) => node.scrollTop)).toBe(200); expect(await detail.evaluate((node) => node.scrollTop)).toBe(500); await expect(runs.getByRole('button', { name: /^History worker 0 / })).toHaveAttribute('aria-current', 'true');
  refusal = 'get_agent_history'; await reconnect(); await expect(page.getByRole('alert')).toContainText('History refresh failed'); await expect(runs.getByRole('button')).toHaveCount(45);
  refusal = ''; await page.getByRole('button', { name: 'Retry history', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0);
  refusal = 'get_agent_history_detail'; await reconnect(); await expect(page.getByRole('alert')).toContainText('Run refresh failed'); await expect(detail.getByText('Refreshed persistent message', { exact: true })).toHaveCount(1);
  refusal = ''; await page.getByRole('button', { name: 'Retry run', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0);
  await detail.evaluate((node) => { node.scrollTop = 0; }); await expect(detail.getByText('333 in / 45 out', { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('history-reconnected.png') });
  await detail.getByRole('button', { name: 'Open task', exact: true }).click(); await expect(page.getByRole('dialog').getByRole('textbox', { name: 'Title', exact: true })).toHaveValue('History linked task'); await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'History', exact: true }).click(); await runs.getByRole('button', { name: /^History worker 0 / }).click(); await detail.getByRole('button', { name: 'Open message task', exact: true }).first().click(); await expect(page.getByRole('dialog').getByRole('textbox', { name: 'Title', exact: true })).toHaveValue('History linked task'); await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'History', exact: true }).click(); await runs.getByRole('button', { name: /^History worker 0 / }).click(); await expect(detail.getByText('Stored task title', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 760, height: 600 }); await expect(page.getByRole('textbox', { name: 'Search history' })).toBeVisible(); await detail.scrollIntoViewIfNeeded(); await expect(detail.getByRole('button', { name: 'Open task', exact: true })).toBeVisible(); await page.screenshot({ path: test.info().outputPath('history-narrow.png') });
  await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); const hidden = reads.length; await reconnect(); expect(reads).toHaveLength(hidden);
});
