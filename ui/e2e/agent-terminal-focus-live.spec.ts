import { expect, test, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;

test('tree activation moves keyboard input into the selected real PTY and honors live focus preferences', async ({ page, request }) => {
  test.setTimeout(90_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires an isolated daemon and explicit local Python runtime');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const command = async (data: Row) => {
    const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
    expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
  };
  const original = (await command({ cmd: 'get_global_settings' })).settings as Row;
  const directory = await mkdtemp(join(tmpdir(), 'torque-tree-focus-'));
  const receiver = fileURLToPath(new URL('./fixtures/streaming_receiver.py', import.meta.url));
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const group = `Tree focus ${Date.now()}`; const ids: string[] = []; const logs = [join(directory, 'first.jsonl'), join(directory, 'second.jsonl')];
  let socket: WebSocketRoute | undefined; let connections = 0; const commands: Row[] = [];
  const received = async (index: number, marker: string) => (await readFile(logs[index]!, 'utf8')).split('\n').some((line) => { if (!line) return false; const row = JSON.parse(line) as Row; return row.kind === 'input' && row.text === marker; });
  const focusPreference = async (enabled: boolean, reload = false) => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('searchbox', { name: 'Search settings' }).fill('Focus on click'); await page.getByRole('button', { name: /^Focus on click — / }).click();
    const input = page.getByRole('combobox', { name: 'Focus on click', exact: true }); await input.selectOption(String(enabled));
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect((await command({ cmd: 'get_global_settings' })).settings).toMatchObject({ focus_on_click: enabled });
    if (reload) { await page.reload(); await page.getByRole('searchbox', { name: 'Search settings' }).fill('Focus on click'); await page.getByRole('button', { name: /^Focus on click — / }).click(); await expect(input).toHaveValue(String(enabled)); }
    await page.getByRole('button', { name: /⌁ Agents/ }).click();
  };
  try {
    await command({ cmd: 'update_global_settings', settings: { focus_on_click: false } });
    await command({ cmd: 'add_group', group });
    await command({ cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic' } });
    for (const index of [0, 1]) {
      const name = `Focus receiver ${index + 1}`;
      const state = await command({ cmd: 'add_agent', group, name, provider: 'generic', command: [python, '-u', receiver, logs[index]!].map(quote).join(' '), directory, shell: '/bin/sh', worktree: false });
      ids.push(Object.values(state.agents as Record<string, Row>).find((agent) => agent.group === group && agent.name === name)!.id as string);
    }
    await command({ cmd: 'ui_select_group', group }); await command({ cmd: 'ui_select_agent', id: ids[0] });
    await command({ cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; const server = client.connectToServer(); client.onMessage((raw) => { commands.push(JSON.parse(String(raw)) as Row); server.send(raw); }); });
    // Keep the real PTY transport, but expose a slow initial open so focus
    // readiness cannot pass merely because loopback connected before the click.
    await page.addInitScript(() => {
      const NativeSocket = window.WebSocket;
      window.WebSocket = new Proxy(NativeSocket, {
        construct(Target, args) {
          const socket = Reflect.construct(Target, args) as WebSocket;
          if (!new URL(String(args[0]), location.href).pathname.startsWith('/ws/terminal/')) return socket;
          let waitingForOpen = true;
          socket.addEventListener('open', (event) => {
            event.stopImmediatePropagation();
            setTimeout(() => {
              waitingForOpen = false;
              if (socket.readyState === NativeSocket.OPEN) socket.dispatchEvent(new Event('open'));
            }, 600);
          }, { once: true });
          return new Proxy(socket, {
            get(target, key) {
              if (key === 'readyState' && waitingForOpen && target.readyState === NativeSocket.OPEN) return NativeSocket.CONNECTING;
              const value: unknown = Reflect.get(target, key, target);
              return typeof value === 'function' ? value.bind(target) as unknown : value;
            },
            set(target, key, value) { return Reflect.set(target, key, value, target); },
          });
        },
      });
    });
    await page.goto('/');
    await focusPreference(true, true); await focusPreference(false, true);
    const first = page.getByRole('treeitem', { name: /^Focus receiver 1,/ }); const second = page.getByRole('treeitem', { name: /^Focus receiver 2,/ });
    const composer = page.getByRole('textbox', { name: 'Message Focus receiver 1', exact: true });
    await page.evaluate(() => {
      const target = window as unknown as { qaMaximumVisibleTerminals: number };
      const count = () => { const visible = [...document.querySelectorAll<HTMLElement>('.xterm')].filter((node) => { const box = node.getBoundingClientRect(); return box.width > 0 && box.height > 0; }).length; target.qaMaximumVisibleTerminals = Math.max(target.qaMaximumVisibleTerminals || 0, visible); };
      count(); new MutationObserver(count).observe(document.body, { childList: true, subtree: true });
    });
    await composer.fill('Retained operator draft');
    await page.getByRole('tab', { name: 'Activity', exact: true }).click();
    await first.click(); await expect(page.getByRole('tab', { name: 'Activity', exact: true })).toHaveAttribute('aria-selected', 'true');
    await first.dblclick(); await expect(page.getByRole('tab', { name: 'Live', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('.xterm-helper-textarea')).toBeFocused(); await page.keyboard.type('double-click-marker'); await page.keyboard.press('Enter');
    await expect.poll(() => received(0, 'double-click-marker')).toBe(true); expect(await received(1, 'double-click-marker')).toBe(false);
    await expect(composer).toHaveText('Retained operator draft');
    await composer.focus(); const count = connections; await socket!.close({ code: 1012, reason: 'Focus retention acceptance' }); await expect.poll(() => connections).toBeGreaterThan(count); await expect(composer).toBeFocused();
    await page.getByRole('tab', { name: 'Activity', exact: true }).click();
    await second.focus(); await second.press('Enter'); await expect(page.locator('.xterm-helper-textarea')).toBeFocused();
    await page.keyboard.type('keyboard-marker'); await page.keyboard.press('Enter'); await expect.poll(() => received(1, 'keyboard-marker')).toBe(true); expect(await received(0, 'keyboard-marker')).toBe(false);
    await focusPreference(true);
    await first.click(); await expect(page.locator('.xterm-helper-textarea')).toBeFocused();
    await page.keyboard.type('preference-marker'); await page.keyboard.press('Enter'); await expect.poll(() => received(0, 'preference-marker')).toBe(true);
    await focusPreference(false);
    await page.getByRole('tab', { name: 'Activity', exact: true }).click(); const before = commands.filter((row) => row.cmd === 'focus_agent').length;
    await first.click(); await expect(page.getByRole('tab', { name: 'Activity', exact: true })).toHaveAttribute('aria-selected', 'true'); expect(commands.filter((row) => row.cmd === 'focus_agent')).toHaveLength(before);
    await page.getByRole('tab', { name: 'Live', exact: true }).click(); await expect(composer).toHaveText('Retained operator draft'); await expect(page.getByRole('region', { name: 'Focus receiver 1 terminal', exact: true }).getByText('connected', { exact: true })).toBeVisible(); await expect(page.locator('.xterm-helper-textarea')).not.toBeFocused();
    expect(await page.evaluate(() => (window as unknown as { qaMaximumVisibleTerminals: number }).qaMaximumVisibleTerminals)).toBe(1);
    await page.screenshot({ path: test.info().outputPath('agent-terminal-focus.png') });
  } finally {
    for (const id of ids) await command({ cmd: 'remove_agent', id });
    await command({ cmd: 'update_global_settings', settings: { focus_on_click: original.focus_on_click } });
    await rm(directory, { recursive: true, force: true });
  }
});
