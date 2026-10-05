import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
for (const phase of ['request', 'body'] as const) test(`raw terminal drops recover from ${phase} deadlines and preserve partial successes and original pane ownership`, async ({ page, request }) => {
  test.setTimeout(110_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires a local Python receiver on an isolated QA daemon');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-terminal-drop-'));
  const log = join(directory, 'received.jsonl'); const script = join(directory, 'receiver.py');
  await writeFile(log, ''); await writeFile(script, 'import json,sys\nprint("DROP_READY",flush=True)\nfor line in sys.stdin:\n with open(sys.argv[1],"a") as output: output.write(json.dumps(line)+"\\n")\n');
  const quote = (value: string) => "'" + value.replaceAll("'", "'\"'\"'") + "'";
  const created: string[] = []; const releases: Record<string, () => void> = {}; const paths: Record<string, string> = {}; const inputs: string[] = [];
  try {
    const group = `Terminal drop ${phase} ${Date.now()}`;
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false } });
    const cell = await command(request, { cmd: 'add_terminal', group, name: 'Drop receiver', command: [python, '-u', script, log].map(quote).join(' '), directory, shell: '/bin/sh' }); created.push(String(cell.id));
    const other = await command(request, { cmd: 'add_terminal', group, name: 'Other drop pane', command: '/bin/cat', directory, shell: '/bin/sh' }); created.push(String(other.id));
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: cell.id });
    await page.addInitScript((phase) => {
      const original = window.fetch.bind(window);
      window.fetch = async (input, options) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.endsWith('/api/upload')) return original(input, options);
        const name = options?.body instanceof FormData ? (options.body.get('file') as File | null)?.name : '';
        const response = await original(input, { ...options, signal: null });
        if (phase === 'body' && name === 'expired.png') {
          const parse = response.json.bind(response);
          response.json = () => new Promise((resolve, reject) => {
            (window as Window & { releaseDropBody?: () => Promise<void> }).releaseDropBody = async () => { try { resolve(await parse()); } catch (error) { reject(error instanceof Error ? error : new Error('Body fixture failed')); } };
          });
        }
        return response;
      };
    }, phase);
    await page.routeWebSocket(/\/ws\/terminal\//, (socket) => {
      const server = socket.connectToServer(); socket.onMessage((raw) => { const frame = JSON.parse(String(raw)) as Row; if (frame.type === 'input') inputs.push(String(frame.data)); server.send(raw); });
    });
    await page.route('**/api/upload', async (route) => {
      const name = /filename="([^"]+)"/u.exec(route.request().postData() ?? '')?.[1] ?? '';
      if (name === 'refused.png') { await route.fulfill({ status: 503, json: { ok: false, error: 'Injected refusal' } }); return; }
      const response = await route.fetch(); const result = await response.json() as { ok: boolean; data: { path: string }[] };
      expect(result.ok).toBe(true); paths[name] = result.data[0]!.path;
      if (name === 'retry.png' || name === 'inactive.png' || name === 'first.png' || (name === 'expired.png' && phase === 'request')) await new Promise<void>((resolve) => { releases[name] = resolve; });
      await route.fulfill({ response });
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const terminal = page.getByRole('region', { name: 'Drop receiver terminal', exact: true });
    await expect(terminal).toBeVisible(); await expect(terminal.getByText('connected', { exact: true })).toBeVisible();
    const bytes = [...await readFile(new URL('./fixtures/composer-preview.png', import.meta.url))];
    const drop = async (...names: string[]) => {
      const transfer = await page.evaluateHandle(({ bytes, names }) => { const data = new DataTransfer(); names.forEach((name) => data.items.add(new File([new Uint8Array(bytes)], name, { type: 'image/png' }))); return data; }, { bytes, names });
      await terminal.locator('.xterm').locator('..').evaluate((node, data) => node.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: data })), transfer); await transfer.dispose();
    };
    await drop('refused.png'); await expect(terminal.getByRole('alert')).toContainText('refused.png'); expect(inputs).toEqual([]);
    await drop('first.png', 'refused.png', 'second.png'); await expect.poll(() => Boolean(paths['second.png'] && releases['first.png'])).toBe(true);
    releases['first.png']!(); await expect.poll(() => inputs.length).toBe(1);
    expect(inputs[0]).toBe(`${quote(paths['first.png']!)} ${quote(paths['second.png']!)} `);
    await expect(terminal.getByRole('alert')).toContainText('Successful image paths were pasted');
    await drop('expired.png'); await expect.poll(() => Boolean(paths['expired.png'])).toBe(true);
    await expect(terminal.getByRole('alert')).toContainText('timed out', { timeout: 35_000 }); expect(inputs).toHaveLength(1);
    await page.setViewportSize({ width: 760, height: 720 }); await expect(terminal.getByRole('alert')).toBeInViewport();
    await page.screenshot({ path: test.info().outputPath(`terminal-drop-${phase}-timeout.png`), animations: 'disabled' });
    await drop('retry.png'); await expect.poll(() => Boolean(releases['retry.png'])).toBe(true);
    if (phase === 'request') { const late = page.waitForResponse((response) => response.url().endsWith('/api/upload')); releases['expired.png']!(); await late; }
    else await page.evaluate(async () => { await (window as Window & { releaseDropBody?: () => Promise<void> }).releaseDropBody?.(); });
    await expect(terminal.getByRole('status')).toHaveText('Uploading terminal images…'); expect(inputs).toHaveLength(1);
    releases['retry.png']!(); await expect.poll(() => inputs.length).toBe(2); expect(inputs[1]).toBe(`${quote(paths['retry.png']!)} `);
    await drop('inactive.png'); await expect.poll(() => Boolean(releases['inactive.png'])).toBe(true);
    await page.locator(`[role="treeitem"][data-agent-id="${String(other.id)}"]`).click();
    await page.locator(`[role="treeitem"][data-agent-id="${String(cell.id)}"]`).click();
    const composer = page.getByRole('textbox', { name: 'Message Drop receiver', exact: true }); await composer.fill('Preserve this focus');
    const late = page.waitForResponse((response) => response.url().endsWith('/api/upload')); releases['inactive.png']!(); await late;
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(inputs).toHaveLength(2); await expect(composer).toBeFocused(); await expect(composer).toHaveValue('Preserve this focus');
    await terminal.locator('.xterm-helper-textarea').press('Enter');
    await expect.poll(async () => (await readFile(log, 'utf8')).trim()).not.toBe('');
    const lines = (await readFile(log, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as string);
    expect(lines).toEqual([`${inputs[0]}${inputs[1]}\n`]);
  } finally {
    Object.values(releases).forEach((release) => release());
    for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});
