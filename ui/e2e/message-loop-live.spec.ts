import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
let created: string[] = []; let release: (() => void) | undefined;
test.afterEach(async ({ request }) => { release?.(); release = undefined; for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id }); created = []; });
test('message loops display scoped timing and cancel with acknowledged retry without affecting drafts or replacement loops', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Loop controls ${Date.now()}`; await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic' } });
  const add = async (name: string) => { const frame = await command(request, { cmd: 'add_agent', group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false }); const agent = Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group && row.name === name)!; created.push(String(agent.id)); return String(agent.id); };
  const worker = await add('Loop worker'); const other = await add('Other loop worker');
  const attached = await command(request, { cmd: 'add_terminal', group, parent_id: worker, name: 'Loop companion', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' }); created.push(String(attached.id));
  const send = (agent: string, message: string) => command(request, { cmd: 'user_agent_message', agent_id: agent, thread_id: `user-agent:user:${agent}`, message, idempotency_key: `loop-fixture-${Date.now()}-${Math.random()}` });
  const first = (await send(worker, '/loop every 1h Check the first loop')).loop as Row; await send(other, '/loop every 2h Other agent loop');
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: worker });
  let socket: WebSocketRoute | undefined; let connections = 0; let mode: 'refuse' | 'lose' | 'normal' | 'hold' = 'refuse'; const requests: Row[] = []; const responses: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'user_agent_message' || data.message !== '/loop cancel') { await route.continue(); return; }
    requests.push(data);
    if (mode === 'refuse') { await route.fulfill({ json: { ok: false, error: 'Injected cancellation refusal' } }); return; }
    if (mode === 'hold') await new Promise<void>((resolve) => { release = resolve; });
    const response = await route.fetch(); const payload = await response.json() as Row; responses.push(payload);
    await route.fulfill(mode === 'lose' ? { json: { ok: false, error: 'Injected lost cancellation acknowledgement' } } : { response });
  });
  await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); const panel = page.getByRole('region', { name: 'Scheduled message loop' });
  await expect(panel).toContainText('Every 1h'); await expect(panel).toContainText('Next '); await expect(panel).toContainText('Check the first loop'); await expect(panel).not.toContainText('Other agent loop');
  const input = page.getByRole('textbox', { name: 'Message Loop worker', exact: true }); await input.fill('Keep this unrelated draft'); await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 8));
  await panel.getByRole('button', { name: 'Cancel loop', exact: true }).click(); await expect(panel.getByRole('alert')).toContainText('Injected cancellation refusal'); await expect(input).toHaveValue('Keep this unrelated draft'); expect(requests[0]).toMatchObject({ agent_id: worker, expected_loop_id: first.id });
  const before = connections; await socket!.close({ code: 1012, reason: 'Loop cancellation retry reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(panel.getByRole('alert')).toContainText('Injected cancellation refusal'); await expect(input).toHaveValue('Keep this unrelated draft');
  mode = 'lose'; await panel.getByRole('button', { name: 'Retry loop cancellation' }).click(); await expect(panel.getByRole('alert')).toContainText('Injected lost cancellation acknowledgement'); expect(requests[1]).toEqual(requests[0]);
  mode = 'normal'; await panel.getByRole('button', { name: 'Retry loop cancellation' }).click(); await expect(panel.getByRole('status')).toHaveText('Message loop cancelled.'); expect(requests[2]).toEqual(requests[0]); expect(responses[1]).toEqual(responses[0]); await expect(input).toHaveValue('Keep this unrelated draft'); expect(await input.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
  await panel.getByRole('button', { name: 'Dismiss loop cancellation status' }).click(); await expect(panel).toHaveCount(0);
  const second = (await send(worker, '/loop every 1h Loop before replacement')).loop as Row; await expect(panel).toContainText('Loop before replacement');
  mode = 'hold'; await panel.getByRole('button', { name: 'Cancel loop', exact: true }).click(); await expect.poll(() => Boolean(release)).toBe(true); await expect(panel.getByRole('button', { name: 'Cancelling loop…' })).toBeDisabled();
  await page.locator(`[role="treeitem"][data-agent-id="${other}"]`).click(); await expect(panel).toContainText('Other agent loop'); await send(worker, '/loop cancel'); const replacement = (await send(worker, '/loop every 1h Replacement stays active')).loop as Row;
  release!(); release = undefined; await expect.poll(() => responses.length).toBe(3); expect(responses[2]).toMatchObject({ ok: false, error: 'The displayed /loop is no longer active. Refresh before cancelling another loop.' }); expect(requests[3]).toMatchObject({ expected_loop_id: second.id });
  await expect(panel).toContainText('Other agent loop'); await page.locator(`[role="treeitem"][data-agent-id="${worker}"]`).click(); await expect(panel).toContainText('Replacement stays active'); await expect(panel).toHaveAttribute('data-message-loop', String(replacement.id)); await expect(input).toHaveValue('Keep this unrelated draft');
  await page.locator(`[role="treeitem"][data-agent-id="${String(attached.id)}"]`).click(); await expect(panel).toContainText('Replacement stays active');
  await page.setViewportSize({ width: 760, height: 720 }); const split = page.getByRole('separator', { name: 'Resize terminal and direct messages', exact: true }); await split.focus(); await split.press('End'); await panel.scrollIntoViewIfNeeded(); await expect(panel.getByRole('button', { name: 'Cancel loop', exact: true })).toBeInViewport(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('message-loop-compact.png') });
  await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect(panel).toContainText('Replacement stays active');
});
