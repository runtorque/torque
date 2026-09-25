import { expect, test, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
interface LiveTerminal {
  options: { fontSize: number; scrollback: number; theme: Record<string, string> };
  cols: number; rows: number;
  buffer: { active: { baseY: number; viewportY: number; length: number; getLine(line: number): { translateToString(): string } | undefined } };
}
interface QaWindow { Terminal: { prototype: { open(this: LiveTerminal, surface: HTMLElement): void } }; qaTerminal?: LiveTerminal; qaTerminalMounts: number }
test('real xterm applies appearance and saved scrollback in place through settings deltas and resync', async ({ page, request }) => {
  test.setTimeout(90_000); test.skip(!process.env.TORQUE_PTY_PYTHON, 'Opt in with an absolute Python executable on the disposable daemon host');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }; expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const command = async (data: Row) => { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); return result.data; };
  const directory = await mkdtemp(join(tmpdir(), 'torque-terminal-preferences-')); const log = join(directory, 'receiver.jsonl'); const receiver = fileURLToPath(new URL('./fixtures/streaming_receiver.py', import.meta.url));
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'"; const group = `Terminal preferences ${Date.now()}`; let agentId = ''; let stateSocket: WebSocketRoute | undefined; let stateConnections = 0; let terminalConnections = 0; let inputToPty: ((data: string) => void) | undefined; const terminalFrames: Row[] = [];
  const inspect = () => page.evaluate(() => { const terminal = (window as unknown as QaWindow).qaTerminal!; const buffer = terminal.buffer.active; return { fontSize: terminal.options.fontSize, scrollback: terminal.options.scrollback, theme: terminal.options.theme, rows: terminal.rows, cols: terminal.cols, base: buffer.baseY, y: buffer.viewportY, line: buffer.getLine(buffer.viewportY)?.translateToString(), mounts: (window as unknown as QaWindow).qaTerminalMounts }; });
  try {
    await command({ cmd: 'update_global_settings', settings: { xterm_scrollback: 300 } }); await command({ cmd: 'add_group', group }); await command({ cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic' } });
    const state = await command({ cmd: 'add_agent', group, name: 'Preference receiver', provider: 'generic', command: [python, '-u', receiver, log].map(quote).join(' '), directory, shell: '/bin/sh', worktree: false }); agentId = Object.values(state.agents as Record<string, Row>).find((agent) => agent.group === group)!.id as string;
    await command({ cmd: 'ui_select_group', group }); await command({ cmd: 'ui_select_agent', id: agentId }); await command({ cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'mission' } });
    await page.addInitScript(() => { if (!localStorage.getItem('torque.appearance.v1')) localStorage.setItem('torque.appearance.v1', JSON.stringify({ contrast: 'balanced', accent: 'violet', scale: 100, terminalFont: 14, density: 'compact', reduceMotion: false })); });
    await page.routeWebSocket(/\/ws\?/, (socket) => { socket.connectToServer(); stateSocket = socket; stateConnections++; });
    await page.routeWebSocket(/\/ws\/terminal\//, (socket) => { terminalConnections++; const server = socket.connectToServer(); inputToPty = (data) => server.send(JSON.stringify({ type: 'input', data })); socket.onMessage((raw) => { terminalFrames.push(JSON.parse(String(raw)) as Row); server.send(raw); }); });
    await page.goto('/'); await page.evaluate(() => { const target = window as unknown as QaWindow;
      // eslint-disable-next-line @typescript-eslint/unbound-method
      const open = target.Terminal.prototype.open; target.qaTerminalMounts = 0; target.Terminal.prototype.open = function (surface) { open.call(this, surface); target.qaTerminal = this; target.qaTerminalMounts++; };
    });
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect(page.getByRole('region', { name: 'Preference receiver terminal' })).toBeVisible(); await expect.poll(async () => (await inspect()).base).toBeGreaterThan(160);
    expect(await inspect()).toMatchObject({ fontSize: 14, scrollback: 300, theme: { cursor: '#a78bfa', selectionBackground: '#a78bfa4d' }, mounts: 1 });
    inputToPty!('pause\r'); await expect.poll(async () => (await readFile(log, 'utf8')).includes('"text": "pause"')).toBe(true);
    await expect.poll(() => page.evaluate(() => { const buffer = (window as unknown as QaWindow).qaTerminal!.buffer.active; return Array.from({ length: buffer.length }, (_, line) => buffer.getLine(line)?.translateToString()).some((line) => line?.includes('RECEIVED:pause')); })).toBe(true);
    await page.locator('.xterm-screen').hover(); await page.mouse.wheel(0, -200); await expect.poll(async () => { const value = await inspect(); return value.base - value.y; }).toBeGreaterThan(5); const reading = await inspect(); const beforeFrames = terminalFrames.length;
    // These are the same root tokens changed by Settings preview/restore. Keep
    // this real terminal mounted while exercising its document observer.
    await page.evaluate(() => { const root = document.documentElement; root.style.setProperty('--terminal-font-size', '18px'); root.style.setProperty('--accent', '#2dd4bf'); root.dataset.torqueContrast = 'high'; });
    await expect.poll(async () => (await inspect()).fontSize).toBe(18); await expect.poll(async () => (await inspect()).rows).not.toBe(reading.rows); await expect.poll(async () => { const value = await inspect(); return value.base - value.y; }).toBe(reading.base - reading.y);
    expect((await inspect()).theme).toMatchObject({ cursor: '#2dd4bf', selectionBackground: '#2dd4bf4d', background: '#08090c', foreground: '#fff' }); expect(terminalFrames.slice(beforeFrames).every((frame) => frame.type === 'resize')).toBe(true);
    await page.screenshot({ path: test.info().outputPath('terminal-appearance-preview.png') });
    await page.evaluate(() => { const root = document.documentElement; root.style.setProperty('--terminal-font-size', '14px'); root.style.setProperty('--accent', '#a78bfa'); root.dataset.torqueContrast = 'balanced'; });
    await expect.poll(async () => (await inspect()).fontSize).toBe(14); await expect.poll(async () => (await inspect()).rows).toBe(reading.rows); await expect.poll(async () => { const value = await inspect(); return value.base - value.y; }).toBe(reading.base - reading.y);
    const beforeShrink = await inspect(); await command({ cmd: 'update_global_settings', settings: { xterm_scrollback: 100 } }); await expect.poll(async () => (await inspect()).scrollback).toBe(100); await expect.poll(async () => (await inspect()).base).toBeLessThanOrEqual(100); expect((await inspect()).line).toBe(beforeShrink.line);
    await command({ cmd: 'update_global_settings', settings: { xterm_scrollback: 600 } }); await expect.poll(async () => (await inspect()).scrollback).toBe(600); expect((await inspect()).mounts).toBe(1); expect(terminalConnections).toBe(1);
    const connectionsBefore = stateConnections; await stateSocket!.close({ code: 1012, reason: 'Terminal preference resync' }); await command({ cmd: 'update_global_settings', settings: { xterm_scrollback: 800 } }); await expect.poll(() => stateConnections).toBeGreaterThan(connectionsBefore); await expect.poll(async () => (await inspect()).scrollback).toBe(800); expect((await inspect()).mounts).toBe(1); expect(terminalConnections).toBe(1);
    await page.getByRole('button', { name: 'Tail', exact: true }).click(); await expect.poll(async () => { const value = await inspect(); return value.base - value.y; }).toBe(0);
    const tail = await inspect(); await page.evaluate(() => document.documentElement.style.setProperty('--terminal-font-size', '16px')); await expect.poll(async () => (await inspect()).rows).not.toBe(tail.rows); await expect.poll(async () => { const value = await inspect(); return value.base - value.y; }).toBe(0);
    expect((await command({ cmd: 'get_global_settings' })).settings).toMatchObject({ xterm_scrollback: 800 }); expect(JSON.parse((await page.evaluate(() => localStorage.getItem('torque.appearance.v1')))!)).toMatchObject({ terminalFont: 14, accent: 'violet' });
    await page.screenshot({ path: test.info().outputPath('terminal-preferences-applied.png') });
  } finally { if (agentId) await command({ cmd: 'remove_agent', id: agentId }); await rm(directory, { recursive: true, force: true }); }
});
