import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}

test('composer deadlines retain reviewed intent and recover real delivery and cancellation receipts', async ({ page, request }) => {
  test.setTimeout(140_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires a Python receiver on the disposable daemon host');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-composer-recovery-'));
  const log = join(directory, 'received.jsonl'); const script = join(directory, 'receiver.py');
  await writeFile(log, '');
  await writeFile(script, 'import json,sys\nprint("RECOVERY_READY",flush=True)\nfor line in sys.stdin:\n with open(sys.argv[1],"a") as output: output.write(json.dumps(line)+"\\n")\n print("RECOVERY_RX:"+line.rstrip(),flush=True)\n');
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const created: string[] = []; let releaseSend: (() => void) | undefined; let releaseRetry: (() => void) | undefined; let releaseCancel: (() => void) | undefined;
  try {
    const group = `Composer recovery ${Date.now()}`;
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic' } });
    const worker = await command(request, { cmd: 'add_worker', group, name: 'Recovery worker', provider: 'generic', command: '/bin/cat', directory, shell: '/bin/sh', worktree: false }); created.push(String(worker.id));
    const shell = await command(request, { cmd: 'add_terminal', group, name: 'Recovery shell', command: [python, '-u', script, log].map(quote).join(' '), directory, shell: '/bin/sh' }); created.push(String(shell.id));
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: shell.id });
    // Let a transport ignore abort so the test can deliver the expired response
    // after a new retry begins. The application's observation must still settle.
    await page.addInitScript(() => {
      const original = window.fetch.bind(window);
      window.fetch = (input, options) => {
        const body = typeof options?.body === 'string' ? JSON.parse(options.body) as { cmd?: string } : {};
        return original(input, ['send_user_message', 'user_agent_turn_cancel'].includes(body.cmd ?? '') ? { ...options, signal: null } : options);
      };
    });
    const sends: Row[] = []; const sendReplies: Row[] = []; const cancels: Row[] = []; const cancelReplies: Row[] = [];
    let refuse = false; let socket: WebSocketRoute | undefined; let connections = 0;
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'send_user_message') {
        sends.push(data);
        if (refuse) { await route.fulfill({ json: { ok: false, error: 'Verified test preflight refusal', delivery_refused: true, command: data.cmd, idempotency_key: data.idempotency_key } }); return; }
        const response = await route.fetch(); sendReplies.push(await response.json() as Row);
        if (sends.length === 1) await new Promise<void>((resolve) => { releaseSend = resolve; });
        else if (sends.length === 2) await new Promise<void>((resolve) => { releaseRetry = resolve; });
        await route.fulfill({ response }); return;
      }
      if (data.cmd === 'user_agent_turn_cancel') {
        cancels.push(data); const response = await route.fetch(); cancelReplies.push(await response.json() as Row);
        if (cancels.length === 1) await new Promise<void>((resolve) => { releaseCancel = resolve; });
        await route.fulfill({ response }); return;
      }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const select = async (id: unknown) => page.locator(`[role="treeitem"][data-agent-id="${String(id)}"]`).click();
    const shellInput = page.getByRole('textbox', { name: 'Message Recovery shell', exact: true });
    const workerInput = page.getByRole('textbox', { name: 'Message Recovery worker', exact: true });
    const marker = `RECOVER_ONCE_${Date.now()}`;
    await shellInput.fill(marker); await shellInput.press('Enter');
    await expect.poll(() => Boolean(releaseSend)).toBe(true);
    await expect.poll(async () => (await readFile(log, 'utf8')).split(marker).length - 1).toBe(1);
    await select(worker.id); await workerInput.fill('Keep this other draft'); await select(shell.id);
    await expect(page.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(shellInput).toHaveAttribute('readonly'); await expect(shellInput).toHaveValue(marker);
    const before = connections; await socket!.close({ code: 1012, reason: 'Unknown composer send' }); await expect.poll(() => connections).toBeGreaterThan(before); expect(sends).toHaveLength(1);
    await page.setViewportSize({ width: 760, height: 720 });
    await page.getByRole('button', { name: 'Retry delivery', exact: true }).scrollIntoViewIfNeeded(); await expect(page.getByRole('button', { name: 'Review delivery', exact: true })).toBeInViewport();
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-unknown-compact.png') });
    await page.getByRole('button', { name: 'Review delivery', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Review uncertain delivery' })).toContainText('may already have been sent');
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-review-compact.png') });
    await page.getByRole('button', { name: 'Keep recovering', exact: true }).click(); await expect(shellInput).toHaveAttribute('readonly');
    await page.getByRole('button', { name: 'Retry delivery', exact: true }).click(); await expect.poll(() => Boolean(releaseRetry)).toBe(true);
    releaseSend!(); releaseSend = undefined; await expect(page.getByRole('button', { name: 'Sending…', exact: true })).toBeDisabled(); await expect(shellInput).toHaveValue(marker);
    releaseRetry!(); releaseRetry = undefined; await expect(shellInput).toHaveValue(''); expect(sends[1]).toEqual(sends[0]); expect(sendReplies[1]).toEqual(sendReplies[0]); expect((await readFile(log, 'utf8')).split(marker).length - 1).toBe(1);
    refuse = true; await shellInput.fill('Refused draft'); await shellInput.press('Enter'); await expect(page.getByRole('alert')).toContainText('Verified test preflight refusal'); await expect(shellInput).not.toHaveAttribute('readonly'); await expect(shellInput).toHaveValue('Refused draft');
    refuse = false; const corrected = `CORRECTED_${Date.now()}`; await shellInput.fill(corrected); await shellInput.press('Enter'); await expect(shellInput).toHaveValue(''); expect(sends[3]!.idempotency_key).not.toBe(sends[2]!.idempotency_key); await expect.poll(async () => (await readFile(log, 'utf8')).includes(corrected)).toBe(true); expect((await readFile(log, 'utf8')).includes('Refused draft')).toBe(false);
    await select(worker.id); await expect(workerInput).toHaveValue('Keep this other draft'); await workerInput.press('Enter'); await expect(workerInput).toHaveValue('');
    await workerInput.fill('Retain while cancellation recovers'); await page.getByRole('button', { name: 'Cancel turn', exact: true }).click(); await expect.poll(() => Boolean(releaseCancel)).toBe(true);
    await select(shell.id); await select(worker.id); await expect(page.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(workerInput).toHaveValue('Retain while cancellation recovers');
    await page.getByRole('button', { name: 'Cancel turn', exact: true }).click(); await expect(page.getByRole('status').filter({ hasText: /cannot safely interrupt|No active turn remains|Queued message cancelled/ })).toBeVisible();
    expect(cancels[1]).toEqual(cancels[0]); expect(cancelReplies[1]).toEqual(cancelReplies[0]); releaseCancel!(); releaseCancel = undefined;
    await expect(workerInput).toHaveValue('Retain while cancellation recovers'); await test.info().attach('actual-terminal-receipts', { body: await readFile(log), contentType: 'application/jsonl' });
  } finally {
    releaseSend?.(); releaseRetry?.(); releaseCancel?.();
    for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});
