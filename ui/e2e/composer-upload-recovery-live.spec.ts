import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
for (const phase of ['request', 'body'] as const) test(`composer upload ${phase} deadline retains offscreen drafts and ignores late images during explicit retry`, async ({ page, request }) => {
  test.setTimeout(95_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires a Python receiver on the disposable daemon host');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-upload-recovery-'));
  const log = join(directory, 'received.jsonl'); const script = join(directory, 'receiver.py');
  await writeFile(log, '');
  await writeFile(script, 'import json,sys\nprint("UPLOAD_READY",flush=True)\nfor line in sys.stdin:\n with open(sys.argv[1],"a") as output: output.write(json.dumps(line)+"\\n")\n');
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const created: string[] = []; const releases: (() => void)[] = []; const uploads: Row[][] = []; const sends: Row[] = [];
  try {
    const group = `Upload ${phase} recovery ${Date.now()}`;
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false } });
    const shell = await command(request, { cmd: 'add_terminal', group, name: 'Upload shell', command: [python, '-u', script, log].map(quote).join(' '), directory, shell: '/bin/sh' }); created.push(String(shell.id));
    const other = await command(request, { cmd: 'add_terminal', group, name: 'Other upload shell', command: '/bin/cat', directory, shell: '/bin/sh' }); created.push(String(other.id));
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: shell.id });
    // Exercise an adapter that ignores abort, including a response whose body
    // does not settle until after another upload has acquired the source draft.
    await page.addInitScript((phase) => {
      const original = window.fetch.bind(window); let uploads = 0;
      window.fetch = async (input, options) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.endsWith('/api/attachment/upload')) return original(input, options);
        const index = uploads++; const response = await original(input, { ...options, signal: null });
        if (phase === 'body' && index === 0) {
          const parse = response.json.bind(response);
          response.json = () => new Promise((resolve, reject) => {
            (window as Window & { releaseUploadBody?: () => Promise<void> }).releaseUploadBody = async () => { try { resolve(await parse()); } catch (error) { reject(error instanceof Error ? error : new Error('Fixture body failed')); } };
          });
        }
        return response;
      };
    }, phase);
    let socket: WebSocketRoute | undefined; let connections = 0;
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/attachment/upload', async (route) => {
      const response = await route.fetch(); const result = await response.json() as { ok: boolean; data: Row[] };
      expect(result.ok).toBe(true); const index = uploads.length; uploads.push(result.data);
      if (index === 1 || phase === 'request') await new Promise<void>((resolve) => { releases[index] = resolve; });
      await route.fulfill({ response });
    });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (['send_user_message', 'user_agent_message'].includes(String(data.cmd))) sends.push(data);
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const input = page.getByRole('textbox', { name: 'Message Upload shell', exact: true });
    await input.fill('Ready '); await input.press('End');
    const bytes = await readFile(new URL('./fixtures/composer-preview.png', import.meta.url));
    const attach = (name: string) => page.locator('input[type=file]').setInputFiles({ name, mimeType: 'image/png', buffer: bytes });
    await attach('expired.png'); await expect.poll(() => uploads.length).toBe(1);
    await page.locator(`[role="treeitem"][data-agent-id="${String(other.id)}"]`).click();
    const otherInput = page.getByRole('textbox', { name: 'Message Other upload shell', exact: true }); await otherInput.fill('Unrelated draft');
    const before = connections; await socket!.close({ code: 1012, reason: 'Upload deadline offscreen' }); await expect.poll(() => connections).toBeGreaterThan(before);
    // The source composer is unmounted. Let its real observation deadline expire.
    await page.waitForTimeout(30_500);
    await expect(otherInput).toHaveValue('Unrelated draft'); expect(uploads).toHaveLength(1); expect(sends).toHaveLength(0); expect(await readFile(log, 'utf8')).toBe('');
    await page.locator(`[role="treeitem"][data-agent-id="${String(shell.id)}"]`).click();
    await expect(page.getByRole('alert')).toContainText('upload timed out'); await expect(input).toHaveValue('Ready ');
    await expect(page.getByRole('button', { name: 'Attach', exact: true })).toBeEnabled(); await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
    await page.setViewportSize({ width: 760, height: 720 }); await page.getByRole('button', { name: 'Attach', exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('alert')).toBeInViewport(); await page.screenshot({ path: test.info().outputPath(`upload-${phase}-timeout.png`), animations: 'disabled' });
    await attach('accepted.png'); await expect.poll(() => uploads.length).toBe(2);
    if (phase === 'request') {
      const late = page.waitForResponse((response) => response.url().endsWith('/api/attachment/upload'));
      releases[0]!(); await late;
    } else await page.evaluate(async () => { await (window as Window & { releaseUploadBody?: () => Promise<void> }).releaseUploadBody?.(); });
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByRole('button', { name: 'Uploading…', exact: true })).toBeDisabled(); await expect(input.locator('[data-composer-image]')).toHaveCount(0);
    releases[1]!(); await expect(input.locator('[data-composer-image]')).toHaveCount(1);
    await expect(input.getByRole('button', { name: /accepted.png/ })).toBeVisible(); expect(sends).toHaveLength(0); expect(await readFile(log, 'utf8')).toBe('');
    await page.getByRole('button', { name: 'Send', exact: true }).click(); await expect(input.locator('[data-composer-image]')).toHaveCount(0); await expect(input).toHaveValue('');
    await expect.poll(async () => (await readFile(log, 'utf8')).trim()).not.toBe('');
    const received = (await readFile(log, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as string);
    expect(received).toHaveLength(1); expect(received[0]).toContain(String(uploads[1]![0]!.path)); expect(received[0]).not.toContain(String(uploads[0]![0]!.path));
    expect(sends).toHaveLength(1);
    await page.locator(`[role="treeitem"][data-agent-id="${String(other.id)}"]`).click(); await expect(otherInput).toHaveValue('Unrelated draft');
  } finally {
    releases.forEach((release) => release());
    for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});
