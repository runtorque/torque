import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Aggregate Chat retains selected thread, expanded history and reading state through a real reconnect snapshot', async ({ page, request }) => {
  test.setTimeout(45_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Chat reconnect ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'chat' } });
  const messages = Array.from({ length: 65 }, (_, index) => ({ id: `qa-chat-${index}`, message: `Retained Chat message ${index}`, sender_name: 'QA Sender', recipient_name: 'QA Recipient', context: { summary: 'QA reading context', task_ids: ['qa-context-task'] } }));
  let socket: WebSocketRoute | undefined; let connections = 0; let refreshed = false; let snapshots = 0;
  const reads: string[] = [];
  page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/cmd') reads.push(String((request.postDataJSON() as Row).cmd)); });
  // Only the peer payload is a deterministic read-only fixture. The daemon
  // connection, reconnect handshake, snapshot projection and mounted UI are real.
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((raw) => {
      const frame = JSON.parse(String(raw)) as Row;
      if (frame.type === 'state') {
        frame.agent_peer_threads = {
          first: { thread_id: 'first', title: 'Chat background group', last_activity_at: refreshed ? 400 : 200, messages: [] },
          selected: { thread_id: 'selected', title: 'Chat selected group', last_activity_at: 100, messages: refreshed ? [...messages, { id: 'qa-chat-new', message: 'New reconnect message' }] : messages },
        }; snapshots++;
      }
      client.send(JSON.stringify(frame));
    });
  });
  await page.goto('/');
  const chat = page.getByRole('region', { name: 'Aggregate peer Chat', exact: true });
  const selected = chat.getByRole('button', { name: /Chat selected group/ }); await selected.click();
  const detail = chat.getByRole('region', { name: 'Peer thread messages', exact: true });
  await expect(detail.getByText('Retained Chat message 0', { exact: true })).toHaveCount(0);
  await detail.getByRole('button', { name: /Load older messages/ }).click();
  const first = detail.getByText('Retained Chat message 0', { exact: true }).locator('..');
  await first.getByText('Context details', { exact: true }).click(); await expect(first.getByText('qa-context-task', { exact: true })).toBeVisible();
  const scroll = detail.locator(':scope > div'); await scroll.evaluate((node) => { node.scrollTop = 240; node.dispatchEvent(new Event('scroll', { bubbles: true })); }); await expect.poll(() => scroll.evaluate((node) => node.scrollTop)).toBe(240);
  const search = chat.getByLabel('Search peer threads', { exact: true }); await search.fill('Chat'); await search.focus(); await search.evaluate((node: HTMLInputElement) => { node.setSelectionRange(1, 3); node.dataset.chatAnchor = 'retained'; });
  refreshed = true; const before = connections; const beforeSnapshots = snapshots; await socket!.close({ code: 1012, reason: 'Chat snapshot continuity' });
  await expect.poll(() => connections).toBeGreaterThan(before); await expect.poll(() => snapshots).toBeGreaterThan(beforeSnapshots);
  await expect(detail.getByText('New reconnect message', { exact: true })).toHaveCount(1); await expect(selected).toHaveAttribute('aria-pressed', 'true');
  await expect(detail.getByText('Retained Chat message 0', { exact: true })).toHaveCount(1); await expect(first.locator('details')).toHaveAttribute('open', '');
  await expect(search).toBeFocused(); await expect(search).toHaveValue('Chat'); await expect(search).toHaveAttribute('data-chat-anchor', 'retained'); expect(await search.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 3]);
  expect(await scroll.evaluate((node) => node.scrollTop)).toBe(240);
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('chat-reconnected.png') });
  await page.getByRole('button', { name: /▦ Board/ }).click(); await expect(chat).toHaveCount(0);
  const hidden = connections; await socket!.close({ code: 1012, reason: 'Hidden Chat' }); await expect.poll(() => connections).toBeGreaterThan(hidden);
  expect(reads.filter((name) => /peer|thread/.test(name))).toEqual([]);
});
