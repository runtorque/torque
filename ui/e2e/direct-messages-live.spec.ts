import { expect, test, type APIRequestContext, type Locator, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
async function anchor(log: Locator) {
  return log.evaluate((node) => {
    const top = node.getBoundingClientRect().top;
    const row = [...node.querySelectorAll<HTMLElement>('[data-message-id]')].find((item) => item.getBoundingClientRect().bottom > top);
    return { id: row?.dataset.messageId, offset: row ? row.getBoundingClientRect().top - top : 0 };
  });
}
test('retained direct messages preserve reading anchors, render safe rich content and copy exact source', async ({ page, request, context }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `DM reading ${Date.now()}`; const created: string[] = []; const ids: string[] = [];
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic' } });
    const frame = await command(request, { cmd: 'add_agent', group, name: 'Reading worker', provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false });
    const worker = Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group)!; created.push(String(worker.id));
    const shell = await command(request, { cmd: 'add_terminal', group, name: 'Reading shell', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' }); created.push(String(shell.id));
    const send = async (message: string, extra: Row = {}) => command(request, { cmd: 'user_agent_message', agent_id: worker.id, thread_id: `user-agent:user:${String(worker.id)}`, message, idempotency_key: `reading-${Date.now()}-${ids.length}`, ...extra });
    for (let index = 0; index < 90; index++) ids.push(String((await send(`Retained message ${index}`)).message_id));
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: worker.id });
    let socket: WebSocketRoute | undefined; let connections = 0;
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const split = page.getByRole('separator', { name: 'Resize terminal and direct messages', exact: true }); await split.focus(); await split.press('End');
    const log = page.getByRole('log', { name: 'Messages with Reading worker' });
    await expect(page.getByText('30 of 90 retained', { exact: true })).toBeVisible(); await expect(log.locator('[data-message-id]')).toHaveCount(30);
    const initial = await anchor(log); await page.getByRole('button', { name: 'Load older messages (60)', exact: true }).click(); await expect(log.locator('[data-message-id]')).toHaveCount(60);
    await expect.poll(async () => (await anchor(log)).id).toBe(initial.id); expect(Math.abs((await anchor(log)).offset - initial.offset)).toBeLessThan(2);
    await log.evaluate((node) => { node.scrollTop = 700; }); await expect(page.getByRole('button', { name: 'Latest messages', exact: true })).toHaveAttribute('aria-pressed', 'false');
    const reading = await anchor(log); const selectedRow = log.locator(`[data-message-id="${reading.id!}"]`); await selectedRow.evaluate((node) => { node.dataset.readingAnchor = 'same-node'; });
    const input = page.getByRole('textbox', { name: 'Message Reading worker', exact: true }); await input.fill('Keep this composer draft'); await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 8));
    for (let index = 90; index < 105; index++) ids.push(String((await send(`Retained message ${index}`)).message_id));
    await expect(page.getByText(/of 100 retained/)).toBeVisible(); await expect.poll(async () => (await anchor(log)).id).toBe(reading.id); expect(Math.abs((await anchor(log)).offset - reading.offset)).toBeLessThan(2);
    await expect(selectedRow).toHaveAttribute('data-reading-anchor', 'same-node'); await expect(input).toBeFocused(); expect(await input.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    const before = connections; await socket!.close({ code: 1012, reason: 'Direct message reading reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect.poll(async () => (await anchor(log)).id).toBe(reading.id); expect(Math.abs((await anchor(log)).offset - reading.offset)).toBeLessThan(2); await expect(selectedRow).toHaveAttribute('data-reading-anchor', 'same-node'); await expect(input).toHaveValue('Keep this composer draft');
    await page.getByRole('tab', { name: 'Activity', exact: true }).click(); await page.getByRole('tab', { name: 'Live', exact: true }).click(); await expect.poll(async () => (await anchor(log)).id).toBe(reading.id);
    await page.locator(`[role="treeitem"][data-agent-id="${String(shell.id)}"]`).click(); await page.locator(`[role="treeitem"][data-agent-id="${String(worker.id)}"]`).click(); await expect.poll(async () => (await anchor(log)).id).toBe(reading.id); expect(Math.abs((await anchor(log)).offset - reading.offset)).toBeLessThan(2);
    const beforeOlder = await log.locator('[data-message-id]').count(); const afterOlder = Math.min(100, beforeOlder + 30);
    await log.evaluate((node) => { node.scrollTop = 0; }); await expect(log.locator('[data-message-id]')).toHaveCount(afterOlder);
    if (afterOlder < 100) await page.getByRole('button', { name: /^Load older messages/ }).click(); await expect(log.locator('[data-message-id]')).toHaveCount(100); await expect(log.locator(`[data-message-id="${ids[0]!}"]`)).toHaveCount(0); await expect(log.locator(`[data-message-id="${ids[5]!}"]`)).toHaveCount(1);
    await page.getByRole('button', { name: 'Latest messages', exact: true }).click();
    const source = '# Reading checkpoint\n\n**Bold** and *emphasis* with `inline`.\n\n- First item\n- Second item\n\n```js\nconst value = "<safe>";\n  value;\n```\n\n[Safe](https://example.com/path) [Unsafe](javascript:alert(1))\n\n<img src=x onerror="window.messageInjected=true">';
    const rich = await send(source, { reply_to_id: ids[104] }); ids.push(String(rich.message_id));
    const message = log.locator(`[data-message-id="${String(rich.message_id)}"]`); await expect(message).toBeAttached(); await expect.poll(() => log.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(2);
    await expect(message.getByRole('heading', { name: 'Reading checkpoint' })).toBeVisible(); await expect(message).toHaveAttribute('aria-label', 'Message from User'); await expect(message).toContainText('In reply to: Retained message 104'); await expect(message.locator('time')).toHaveAttribute('datetime', /T/); await expect(message.getByRole('link', { name: 'Safe', exact: true })).toHaveAttribute('rel', 'noopener noreferrer'); await expect(message.getByRole('link', { name: 'Unsafe', exact: true })).toHaveCount(0); await expect(message.locator('img, script')).toHaveCount(0);
    await message.getByRole('button', { name: 'Copy message', exact: true }).click(); await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(source);
    await message.getByRole('button', { name: 'Copy code', exact: true }).click(); await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('const value = "<safe>";\n  value;');
    await message.click({ button: 'right' }); await page.getByRole('menuitem', { name: 'Copy message', exact: true }).click(); await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(source); await expect(page.getByRole('button', { name: 'Cancel reply', exact: true })).toHaveCount(0);
    await page.evaluate(() => { Object.defineProperty(navigator.clipboard, 'writeText', { configurable: true, value: () => Promise.reject(new Error('Injected clipboard refusal')) }); });
    const paragraph = message.locator('p').filter({ hasText: 'Bold and emphasis' }); await paragraph.evaluate((node) => { const range = document.createRange(); range.selectNodeContents(node); const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range); });
    const selectedText = await page.evaluate(() => window.getSelection()?.toString()); await message.getByRole('button', { name: 'Copy message', exact: true }).click(); await expect(page.getByRole('status').filter({ hasText: 'Could not copy message' })).toBeVisible(); expect(await page.evaluate(() => window.getSelection()?.toString())).toBe(selectedText); await expect(input).toHaveValue('Keep this composer draft');
    // Viewport resizing finishes before ResizeObserver publishes the new
    // separator limit. Wait for that observable limit before pressing End.
    const beforeWideMaximum = Number(await split.getAttribute('aria-valuemax'));
    await page.setViewportSize({ width: 1440, height: 1100 });
    await expect.poll(async () => Number(await split.getAttribute('aria-valuemax'))).toBeGreaterThan(beforeWideMaximum);
    await split.focus(); await split.press('End');
    await expect.poll(async () => Math.abs(Number(await split.getAttribute('aria-valuenow')) - Number(await split.getAttribute('aria-valuemax')))).toBeLessThan(1);
    await message.scrollIntoViewIfNeeded(); await expect(message.getByRole('heading', { name: 'Reading checkpoint' })).toBeInViewport();
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('direct-message-reading.png') });
    const beforeCompactMaximum = Number(await split.getAttribute('aria-valuemax'));
    await page.setViewportSize({ width: 760, height: 720 });
    await expect.poll(async () => Number(await split.getAttribute('aria-valuemax'))).toBeLessThan(beforeCompactMaximum);
    await split.focus(); await split.press('End');
    await expect.poll(async () => Math.abs(Number(await split.getAttribute('aria-valuenow')) - Number(await split.getAttribute('aria-valuemax')))).toBeLessThan(1);
    await log.scrollIntoViewIfNeeded(); await expect(log).toBeInViewport(); await expect(message.getByRole('button', { name: 'Copy message', exact: true })).toBeVisible(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('direct-message-compact.png') });
    await split.focus(); await split.press('Home'); await message.getByRole('button', { name: 'Copy message', exact: true }).scrollIntoViewIfNeeded(); await expect(page.getByRole('status').filter({ hasText: 'Could not copy message' })).toBeInViewport(); await expect(message.getByRole('button', { name: 'Copy message', exact: true })).toBeInViewport(); await page.getByRole('button', { name: 'Dismiss copy status', exact: true }).click(); await expect(page.getByRole('status').filter({ hasText: 'Could not copy message' })).toHaveCount(0);
  } finally { for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id }); }
});
