import { expect, test, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';

type Row = Record<string, unknown>;
interface LiveTerminal {
  cols: number;
  rows: number;
  buffer: { active: { baseY: number; viewportY: number; getLine(line: number): { translateToString(): string } | undefined } };
}
interface QaWindow {
  Terminal: { prototype: { open(this: LiveTerminal, surface: HTMLElement): void } };
  qaTerminal?: LiveTerminal;
  qaTerminalMounts: number;
}

test('real xterm preserves scrollback and tail pinning across live output, fit and reconnect', async ({ page, request }) => {
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Opt in with an absolute Python executable on the disposable daemon host');
  const python = process.env.TORQUE_PTY_PYTHON!;
  expect(isAbsolute(python)).toBe(true);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932);
  expect(runtime.data.runtime.profile).not.toBe('default');
  const command = async (data: Row) => {
    const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
    expect(result.ok, result.error).toBe(true); return result.data;
  };
  const directory = await mkdtemp(join(tmpdir(), 'torque-terminal-e2e-'));
  const log = join(directory, 'receiver.jsonl');
  const receiver = fileURLToPath(new URL('./fixtures/streaming_receiver.py', import.meta.url));
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const group = `Terminal parity ${Date.now()}`;
  let agentId = '';
  let terminalSocket: WebSocketRoute | undefined;
  let connections = 0;
  let snapshots = 0;
  const frames: Row[] = [];
  try {
    await command({ cmd: 'add_group', group });
    await command({ cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic' } });
    const state = await command({ cmd: 'add_agent', group, name: 'Scroll receiver', provider: 'generic', command: [python, '-u', receiver, log].map(quote).join(' '), directory, shell: '/bin/sh', worktree: false });
    agentId = Object.values(state.agents as Record<string, Row>).find((agent) => agent.group === group)!.id as string;
    await command({ cmd: 'ui_select_group', group });
    await command({ cmd: 'ui_select_agent', id: agentId });
    await page.routeWebSocket(/\/ws\/terminal\//, (socket) => {
      connections += 1; terminalSocket = socket;
      const server = socket.connectToServer();
      socket.onMessage((raw) => { frames.push(JSON.parse(String(raw)) as Row); server.send(raw); });
      server.onMessage((raw) => {
        if ((JSON.parse(String(raw)) as Row).type === 'snapshot') snapshots += 1;
        socket.send(raw);
      });
    });
    await page.goto('/');
    await page.evaluate(() => {
      const target = window as unknown as QaWindow;
      // Preserve the original receiver explicitly with open.call below.
      // eslint-disable-next-line @typescript-eslint/unbound-method
      const open = target.Terminal.prototype.open;
      target.qaTerminalMounts = 0;
      target.Terminal.prototype.open = function (surface) { open.call(this, surface); target.qaTerminal = this; target.qaTerminalMounts += 1; };
    });
    await page.getByRole('button', { name: /⌁ Agents/ }).click();
    await expect(page.getByRole('region', { name: 'Scroll receiver terminal' })).toBeVisible();
    const viewport = () => page.evaluate(() => {
      const terminal = (window as unknown as QaWindow).qaTerminal!;
      const buffer = terminal.buffer.active;
      return { base: buffer.baseY, y: buffer.viewportY, line: buffer.getLine(buffer.viewportY)?.translateToString(), rows: terminal.rows, cols: terminal.cols };
    });
    await expect.poll(async () => (await viewport()).base).toBeGreaterThan(50);
    await expect.poll(() => frames.some((frame) => frame.type === 'resize')).toBe(true);
    const hydrated = await viewport();
    // Let the initial snapshot/fit finish before exercising a user scroll.
    await expect.poll(async () => (await viewport()).base).toBeGreaterThan(hydrated.base + 10);
    await page.locator('.xterm-screen').hover();
    await page.mouse.wheel(0, -400);
    await expect.poll(async () => { const value = await viewport(); return value.base - value.y; }).toBeGreaterThan(10);
    const reading = await viewport();
    await expect.poll(async () => (await viewport()).base).toBeGreaterThan(reading.base + 10);
    expect((await viewport()).line).toBe(reading.line);
    expect((await viewport()).y).toBe(reading.y);

    await page.getByRole('button', { name: 'Tail', exact: true }).click();
    await expect.poll(async () => { const value = await viewport(); return value.base - value.y; }).toBe(0);
    const beforeFit = await viewport();
    await page.setViewportSize({ width: 1440, height: 960 });
    await expect.poll(async () => (await viewport()).rows).not.toBe(beforeFit.rows);
    await expect.poll(async () => { const value = await viewport(); return value.base - value.y; }).toBe(0);

    const input = page.getByRole('textbox', { name: 'Terminal input', exact: true });
    await input.pressSequentially('pause'); await input.press('Enter');
    await expect.poll(async () => (await readFile(log, 'utf8')).includes('"text": "pause"')).toBe(true);
    await page.locator('.xterm-screen').hover(); await page.mouse.wheel(0, -400);
    await expect.poll(async () => { const value = await viewport(); return value.base - value.y; }).toBeGreaterThan(10);
    const beforeReconnect = await viewport();
    const beforeFrames = frames.filter((frame) => frame.type === 'resize').length;
    const beforeSnapshots = snapshots;
    await terminalSocket!.close({ code: 1012, reason: 'PTY reconnect parity' });
    await expect.poll(() => connections).toBe(2);
    await expect.poll(() => snapshots).toBeGreaterThan(beforeSnapshots);
    await expect.poll(() => frames.filter((frame) => frame.type === 'resize').length).toBeGreaterThan(beforeFrames);
    await expect.poll(async () => { const value = await viewport(); return value.base - value.y; }).toBe(beforeReconnect.base - beforeReconnect.y);
    expect(await page.evaluate(() => (window as unknown as QaWindow).qaTerminalMounts)).toBe(1);
    await page.screenshot({ path: test.info().outputPath('terminal-scrollback.png'), fullPage: true });
  } finally {
    if (agentId) await command({ cmd: 'remove_agent', id: agentId });
    await rm(directory, { recursive: true, force: true });
  }
});
