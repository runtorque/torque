import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';

type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}

test('composers retain per-cell drafts, acknowledge real terminal delivery and route attached replies to the parent', async ({ page, request }) => {
  test.setTimeout(90_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires a local Python receiver on the disposable daemon host');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-composer-'));
  const log = join(directory, 'received.jsonl'); const script = join(directory, 'receiver.py');
  await writeFile(log, '');
  await writeFile(script, 'import json,sys\nprint("COMPOSER_READY",flush=True)\nfor line in sys.stdin:\n with open(sys.argv[1],"a") as output: output.write(json.dumps(line)+"\\n")\n print("COMPOSER_RX:"+line.rstrip(),flush=True)\n');
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const boot = [python, '-u', script, log].map(quote).join(' ');
  const group = `Composer ${Date.now()}`; const created: string[] = [];
  let release: (() => void) | undefined;
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic' } });
    const state = await command(request, { cmd: 'add_agent', group, name: 'Composer parent', provider: 'generic', command: '/bin/cat', directory, shell: '/bin/sh', worktree: false });
    const parent = Object.values(state.agents as Record<string, Row>).find((row) => row.group === group)!; created.push(String(parent.id));
    const shell = await command(request, { cmd: 'add_terminal', group, name: 'Composer shell', command: boot, directory, shell: '/bin/sh' }); created.push(String(shell.id));
    const attached = await command(request, { cmd: 'add_terminal', group, parent_id: parent.id, name: 'Composer companion', command: '/bin/cat', directory, shell: '/bin/sh' }); created.push(String(attached.id));
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: shell.id });
    let socket: WebSocketRoute | undefined; let connections = 0;
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    const sends: Row[] = []; const dms: Row[] = []; const cancels: Row[] = [];
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'send_user_message') {
        sends.push(data); const response = await route.fetch();
        if (sends.length === 1) { await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: false, error: 'Simulated lost delivery acknowledgement' } }); return; }
        await route.fulfill({ response }); return;
      }
      if (data.cmd === 'user_agent_message') dms.push(data);
      if (data.cmd === 'user_agent_turn_cancel') cancels.push(data);
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const select = async (id: unknown) => page.locator(`[role="treeitem"][data-agent-id="${String(id)}"]`).click();
    const shellInput = page.getByRole('textbox', { name: 'Message Composer shell', exact: true });
    await expect(shellInput).toBeVisible(); const marker = `COMPOSE_${Date.now()}`;
    await shellInput.fill(marker); await shellInput.press('Enter'); await expect.poll(() => Boolean(release)).toBe(true);
    await expect(shellInput).toBeDisabled(); await expect.poll(async () => (await readFile(log, 'utf8')).split(marker).length - 1).toBe(1);
    await select(parent.id); const parentInput = page.getByRole('textbox', { name: 'Message Composer parent', exact: true });
    await parentInput.fill('Unsent parent draft'); release!();
    await select(shell.id); await expect(shellInput).toHaveValue(marker); await expect(page.getByRole('alert')).toContainText('Simulated lost delivery acknowledgement');
    await shellInput.press('Enter'); await expect(shellInput).toHaveValue(''); expect(sends).toHaveLength(2); expect(sends[0]).toEqual(sends[1]);
    expect((await readFile(log, 'utf8')).split(marker).length - 1).toBe(1);
    await shellInput.fill('Unsent shell draft'); await shellInput.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 8));
    await select(parent.id); await expect(parentInput).toHaveValue('Unsent parent draft'); await select(shell.id); await expect(shellInput).toHaveValue('Unsent shell draft');
    expect(await shellInput.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await shellInput.focus(); const before = connections; await socket!.close({ code: 1012, reason: 'Composer draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(shellInput).toHaveValue('Unsent shell draft'); await expect(shellInput).toBeFocused();
    await page.getByRole('button', { name: 'Message history', exact: true }).click(); await page.getByRole('dialog', { name: 'Recent messages' }).getByRole('button', { name: marker, exact: true }).click();
    await expect(shellInput).toHaveValue(marker); await page.getByRole('button', { name: 'Restore draft', exact: true }).click(); await expect(shellInput).toHaveValue('Unsent shell draft');
    const seed = await command(request, { cmd: 'user_agent_message', agent_id: parent.id, message: 'Parent reply target', thread_id: `user-agent:user:${String(parent.id)}`, idempotency_key: `seed-${Date.now()}` });
    await select(attached.id); await expect(parentInput).toHaveValue('');
    const message = page.locator(`[data-message-id="${String(seed.message_id)}"]`); await message.getByRole('button', { name: 'Reply', exact: true }).click();
    await parentInput.fill('Companion reply');
    await page.locator('input[type="file"]').setInputFiles({ name: 'composer-note.txt', mimeType: 'text/plain', buffer: Buffer.from('Composer attachment') });
    await expect(page.getByRole('alert')).toContainText('Only PNG, JPEG, WebP, or GIF'); await expect(parentInput).toHaveValue('Companion reply');
    await page.locator('input[type="file"]').setInputFiles({ name: 'composer-image.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9X8AAAAASUVORK5CYII=', 'base64') });
    await expect(page.getByRole('button', { name: /composer-image.png/ })).toBeVisible();
    await select(shell.id); await expect(shellInput).toHaveValue('Unsent shell draft'); await select(attached.id); expect(await parentInput.evaluate((node) => { const copy = node.cloneNode(true) as HTMLElement; copy.querySelectorAll('[data-composer-image]').forEach((entry) => entry.remove()); return copy.textContent; })).toBe('Companion reply');
    await expect(page.getByText('Replying to: Parent reply target')).toBeVisible();
    await page.setViewportSize({ width: 760, height: 720 }); await page.getByRole('button', { name: 'Send', exact: true }).scrollIntoViewIfNeeded(); await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeInViewport(); await expect(parentInput).toBeInViewport(); await expect(page.getByText('Replying to: Parent reply target')).toBeInViewport(); await expect(page.getByRole('button', { name: /composer-image.png/ })).toBeInViewport(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-reply-compact.png') });
    await parentInput.press('Enter'); await expect(parentInput.locator('[data-composer-image]')).toHaveCount(0); await expect(parentInput).toHaveValue(''); expect(dms).toHaveLength(1);
    expect(dms[0]).toMatchObject({ agent_id: parent.id, reply_to_id: seed.message_id, thread_id: `user-agent:user:${String(parent.id)}` }); expect(String(dms[0]!.message)).toContain('composer-image.png');
    await parentInput.fill('Keep this after cancellation'); await page.getByRole('button', { name: 'Cancel turn', exact: true }).click();
    await expect.poll(() => cancels.length).toBe(1); expect(cancels[0]).toMatchObject({ agent_id: parent.id, session_id: parent.session_id, turn_idempotency_key: dms[0]!.idempotency_key });
    await expect(page.getByRole('status').filter({ hasText: /cannot safely interrupt|Queued message cancelled|No active turn remains/ })).toBeVisible(); await expect(parentInput).toHaveValue('Keep this after cancellation');
    await page.setViewportSize({ width: 1280, height: 900 }); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-cancellation.png') });
    await select(parent.id); await expect(parentInput).toHaveValue('Unsent parent draft');
    await select(shell.id); await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Message history', exact: true }).click(); await expect(page.getByRole('dialog', { name: 'Recent messages' }).getByRole('button', { name: marker, exact: true })).toBeVisible();
  } finally {
    release?.(); for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});
