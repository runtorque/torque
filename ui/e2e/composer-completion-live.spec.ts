import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
let agents: string[] = []; let tasks: string[] = [];
test.afterEach(async ({ request }) => {
  for (const id of agents.reverse()) await command(request, { cmd: 'remove_agent', id }); agents = [];
  for (const id of tasks) await command(request, { cmd: 'board_remove_task', id }); tasks = [];
});
test('composer discovers provider commands and scoped task references without sending completion choices', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Completions ${Date.now()}`; const other = `${group} other`;
  for (const name of [group, other]) await command(request, { cmd: 'add_group', group: name });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic' } });
  const frame = await command(request, { cmd: 'add_agent', group, name: 'Completion worker', provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false });
  const worker = Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group)!; agents.push(String(worker.id));
  const shell = await command(request, { cmd: 'add_terminal', group, name: 'Completion shell', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' }); agents.push(String(shell.id));
  const attached = await command(request, { cmd: 'add_terminal', group, parent_id: worker.id, name: 'Completion companion', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' }); agents.push(String(attached.id));
  const addTask = async (title: string, scope = group) => { const id = String((await command(request, { cmd: 'board_add_task', group: scope, task: title })).task_id); tasks.push(id); return id; };
  const task = await addTask('Renderer completion target'); await addTask('Renderer outside group', other); const archived = await addTask('Renderer archived'); await command(request, { cmd: 'board_archive_task', id: archived });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: worker.id });
  let socket: WebSocketRoute | undefined; let connections = 0; const sends: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'user_agent_message' || data.cmd === 'send_user_message') sends.push(data); await route.continue(); });
  await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
  const input = page.getByRole('textbox', { name: 'Message Completion worker', exact: true }); await input.fill('/');
  const commands = page.getByRole('listbox', { name: 'Message commands' }); await expect(commands).toBeInViewport(); await expect(commands.getByRole('option', { name: /^\/fast(?: |$)/ })).toHaveCount(0); await expect(commands.getByRole('option', { name: /^\/compact / })).toHaveCount(1);
  await input.fill('/loop every'); await input.press('Tab'); await expect(input).toHaveValue('/loop every 10m '); await expect.poll(() => input.evaluate((node: HTMLTextAreaElement) => node.selectionStart)).toBe(16); await expect(commands).toHaveCount(0); expect(sends).toHaveLength(0);
  await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('/loop every'); await input.press('Escape'); await expect(commands).toHaveCount(0);
  await input.fill('/commands'); await input.press('ArrowDown'); await input.press('Enter'); await expect(input).toHaveValue('/commands'); expect(sends).toHaveLength(0); await input.press('Enter'); await expect(input).toHaveValue(''); expect(sends).toHaveLength(1); expect(sends[0]).toMatchObject({ agent_id: worker.id, message: '/commands' });
  await input.fill('/loop every 0m broken'); await input.press('Enter'); await expect(page.getByRole('alert')).toBeVisible(); await expect(input).toHaveValue('/loop every 0m broken');
  await input.fill('/not-a-command ordinary prose'); await input.press('Enter'); await expect(input).toHaveValue(''); expect(sends.at(-1)).toMatchObject({ message: '/not-a-command ordinary prose' });
  await input.fill('Please :rend suffix'); await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(12, 12)); await input.press('ArrowLeft'); await input.press('ArrowRight');
  const references = page.getByRole('listbox', { name: 'Task references' }); await expect(references.getByRole('option')).toHaveCount(1); const option = references.getByRole('option', { name: `${task} Renderer completion target`, exact: true }); await expect(option).toBeVisible();
  const before = connections; await socket!.close({ code: 1012, reason: 'Composer completion reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(option).toBeVisible(); await expect(input).toBeFocused(); expect(await input.evaluate((node: HTMLTextAreaElement) => node.selectionStart)).toBe(12);
  const beforePick = sends.length; await option.click(); await expect(input).toHaveValue(`Please ${task}  suffix`); await expect(input).toBeFocused(); expect(await input.evaluate((node: HTMLTextAreaElement) => node.selectionStart)).toBe(8 + task.length); expect(sends).toHaveLength(beforePick);
  await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('Please :rend suffix');
  await page.locator(`[role="treeitem"][data-agent-id="${String(attached.id)}"]`).click(); await input.fill('/commands'); await expect(commands).toBeVisible(); await input.press('Escape');
  await page.locator(`[role="treeitem"][data-agent-id="${String(shell.id)}"]`).click(); const standalone = page.getByRole('textbox', { name: 'Message Completion shell', exact: true }); await standalone.fill('/'); await expect(commands).toHaveCount(0); await standalone.fill(':rend'); await expect(references.getByRole('option')).toHaveCount(1);
  await page.setViewportSize({ width: 760, height: 720 }); await expect(references).toBeInViewport(); await standalone.scrollIntoViewIfNeeded(); await expect(references).toBeInViewport(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-task-completion-compact.png') });
  await standalone.press('Escape'); await page.locator(`[role="treeitem"][data-agent-id="${String(worker.id)}"]`).click(); await input.fill('/'); await expect(commands).toBeInViewport(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-command-completion.png') });
});
