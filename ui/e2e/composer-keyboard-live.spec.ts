import { expect, test, type APIRequestContext, type Locator, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
async function text(input: Locator) {
  return input.evaluate((node) => { if (node instanceof HTMLTextAreaElement) return node.value; const copy = node.cloneNode(true) as HTMLElement; copy.querySelectorAll('[data-composer-image]').forEach((token) => token.replaceWith(document.createTextNode('\ufffc'))); return copy.textContent ?? ''; });
}
test('composer transactions and Escape/Home/End match across textarea, inline images, reconnect and cell changes', async ({ page, request, context }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Keyboard ${Date.now()}`; const created: string[] = []; const cancels: Row[] = [];
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic' } });
    const add = async (name: string) => { const state = await command(request, { cmd: 'add_agent', group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false }); const agent = Object.values(state.agents as Record<string, Row>).find((row) => row.group === group && row.name === name)!; created.push(String(agent.id)); return String(agent.id); };
    const worker = await add('Keyboard worker'); const other = await add('Keyboard other');
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: worker });
    let socket: WebSocketRoute | undefined; let connections = 0;
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'user_agent_turn_cancel') cancels.push(data); await route.continue(); });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const input = page.getByRole('textbox', { name: 'Message Keyboard worker', exact: true });
    await input.pressSequentially('alpha'); await input.press('Shift+Enter'); await input.pressSequentially('beta'); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('');
    await input.press('ControlOrMeta+Shift+z'); await expect(input).toHaveValue('alpha\nbeta');
    expect(await input.evaluate((node) => node.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, metaKey: true, bubbles: true, cancelable: true })))).toBe(true); await expect(input).toHaveValue('alpha\nbeta');
    await input.press('Backspace'); await input.press('Backspace'); await expect(input).toHaveValue('alpha\nbe'); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('alpha\nbeta');
    await input.press('Home'); expect(await input.evaluate((node: HTMLTextAreaElement) => node.selectionStart)).toBe(6);
    await input.press('Shift+End'); expect(await input.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd, node.selectionDirection])).toEqual([6, 10, 'forward']);
    await input.press('ControlOrMeta+Shift+Home'); expect(await input.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd, node.selectionDirection])).toEqual([0, 6, 'backward']);
    await input.press('ControlOrMeta+End'); await input.pressSequentially('X'); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('alpha\nbeta');
    await input.press('ControlOrMeta+End'); await context.grantPermissions(['clipboard-read', 'clipboard-write']); await page.evaluate(() => navigator.clipboard.writeText(' pasted')); await input.press('ControlOrMeta+v'); await expect(input).toHaveValue('alpha\nbeta pasted');
    await input.pressSequentially('Y'); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('alpha\nbeta pasted'); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('alpha\nbeta');
    await input.press('Escape'); await expect(input).toHaveValue(''); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue('alpha\nbeta');
    await input.press('ControlOrMeta+Home'); await page.locator('input[type=file]').setInputFiles({ name: 'keyboard.png', mimeType: 'image/png', buffer: readFileSync(new URL('./fixtures/composer-preview.png', import.meta.url)) });
    await expect(input.locator('[data-composer-image]')).toHaveCount(1); await input.press('ControlOrMeta+End'); await input.pressSequentially('12'); await input.press('ControlOrMeta+z'); expect(await text(input)).toBe('\ufffcalpha\nbeta');
    await input.press('ControlOrMeta+Shift+z'); expect(await text(input)).toBe('\ufffcalpha\nbeta12');
    await input.press('Home'); await input.press('Shift+End'); expect(await input.evaluate(() => window.getSelection()?.toString())).toBe('beta12');
    await input.press('Shift+Home'); expect(await input.evaluate(() => window.getSelection()?.toString())).toBe('');
    await input.press('ControlOrMeta+End'); await input.pressSequentially('Z');
    const before = connections; await socket!.close({ code: 1012, reason: 'Keyboard history reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await page.locator(`[role="treeitem"][data-agent-id="${other}"]`).click(); await page.getByRole('textbox', { name: 'Message Keyboard other', exact: true }).fill('Other draft');
    await page.locator(`[role="treeitem"][data-agent-id="${worker}"]`).click(); await input.press('ControlOrMeta+z'); expect(await text(input)).toBe('\ufffcalpha\nbeta12');
    await input.press('Escape'); await expect(input).toHaveValue(''); await input.press('ControlOrMeta+z'); await expect(input.locator('[data-composer-image]')).toHaveCount(1); expect(await text(input)).toBe('\ufffcalpha\nbeta12');
    await input.press('Escape'); await input.fill('/'); await expect(page.getByRole('listbox', { name: 'Message commands' })).toBeVisible(); await input.press('Escape'); await expect(input).toHaveValue('/'); await expect(page.getByRole('listbox', { name: 'Message commands' })).toHaveCount(0); await input.press('Escape'); await expect(input).toHaveValue('');
    await input.fill('Keyboard submitted'); await input.press('Enter'); await expect(input).toHaveValue('');
    const message = page.locator('[data-message-id]').filter({ hasText: 'Keyboard submitted' }).first(); await message.getByRole('button', { name: 'Reply', exact: true }).click(); await input.fill('Retained draft');
    await input.press('Home'); await input.press('ArrowUp'); await expect(input).toHaveValue('Keyboard submitted');
    await input.press('Escape'); await expect(input).toHaveValue('Retained draft'); await expect(page.getByText('Replying to: Keyboard submitted')).toBeVisible();
    await input.press('Escape'); await expect(page.getByText('Replying to: Keyboard submitted')).toHaveCount(0); await expect(input).toHaveValue('Retained draft');
    await input.press('Escape'); await expect(input).toHaveValue(''); expect(cancels).toHaveLength(0);
    await input.dispatchEvent('keydown', { key: 'Escape', repeat: true }); expect(cancels).toHaveLength(0); await input.press('Escape');
    await expect.poll(() => cancels.length).toBe(1); await expect(page.getByRole('status').filter({ hasText: /cannot safely interrupt|Queued message cancelled|No active turn remains/ })).toBeVisible();
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-keyboard.png') });
    await page.locator(`[role="treeitem"][data-agent-id="${other}"]`).click(); await expect(page.getByRole('textbox', { name: 'Message Keyboard other', exact: true })).toHaveValue('Other draft');
  } finally { for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id }); }
});
