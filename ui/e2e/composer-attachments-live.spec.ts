import { expect, test, type APIRequestContext, type Locator, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
const bytes = readFileSync(new URL('./fixtures/composer-preview.png', import.meta.url));
async function plainText(input: Locator): Promise<string> {
  return input.evaluate((node) => { if (node instanceof HTMLTextAreaElement) return node.value; const copy = node.cloneNode(true) as HTMLElement; copy.querySelectorAll('[data-composer-image]').forEach((entry) => entry.remove()); return copy.textContent ?? ''; });
}
async function besideImage(input: Locator, index: number, after: boolean) {
  await input.focus(); await input.evaluate((node, options) => { const token = node.querySelectorAll('[data-composer-image]')[options.index]!; const range = document.createRange(); if (options.after) range.setStartAfter(token); else range.setStartBefore(token); range.collapse(true); const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range); }, { index, after });
}
test('inline composer images preserve placement, preview, undo, retries and source-cell uploads through reconnect', async ({ page, request }) => {
  test.setTimeout(120_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Image composer ${Date.now()}`; const created: string[] = []; let release: (() => void) | undefined;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic' } });
  const add = async (name: string) => { const frame = await command(request, { cmd: 'add_agent', group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false }); const agent = Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group && row.name === name)!; created.push(String(agent.id)); return String(agent.id); };
  try {
    const worker = await add('Image worker'); const other = await add('Other image worker');
    const terminal = await command(request, { cmd: 'add_terminal', group, name: 'Image terminal', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' }); created.push(String(terminal.id));
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: worker });
    await command(request, { cmd: 'user_agent_message', agent_id: worker, thread_id: `user-agent:user:${worker}`, message: 'Earlier image-test message', idempotency_key: `seed-${Date.now()}` });
    const uploads: Row[] = []; const sends: Row[] = []; let holdUpload = false; let refuseSend = true; let socket: WebSocketRoute | undefined; let connections = 0;
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/attachment/upload', async (route) => { const response = await route.fetch(); const payload = await response.json() as { ok: boolean; data?: Row[] }; if (payload.ok) uploads.push(...payload.data!); if (holdUpload) await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response }); });
    await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (['user_agent_message', 'send_user_message'].includes(String(data.cmd)) && String(data.message ?? data.text).includes('.png')) { sends.push(data); if (refuseSend) { await route.fulfill({ json: { ok: false, error: 'Injected image send refusal' } }); return; } } await route.continue(); });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
    const input = page.getByRole('textbox', { name: 'Message Image worker', exact: true }); const tokens = input.locator('[data-composer-image]'); const fileInput = page.locator('input[type="file"]');
    await input.fill('before after'); await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(7, 7));
    await fileInput.setInputFiles({ name: 'one.png', mimeType: 'image/png', buffer: bytes }); await expect(tokens).toHaveCount(1); expect(await plainText(input)).toBe('before after');
    const preview = page.getByRole('dialog', { name: 'Attached image preview' });
    await tokens.first().click(); await expect(preview.getByRole('img', { name: 'one.png' })).toBeVisible(); await expect.poll(() => preview.getByRole('img').evaluate((node: HTMLImageElement) => node.naturalWidth)).toBe(960); await preview.getByRole('button', { name: 'Close dialog' }).click();
    await besideImage(input, 0, false); await input.press('Delete'); await expect(input).toHaveValue('before after'); await input.press('ControlOrMeta+z'); await expect(tokens).toHaveCount(1);
    await besideImage(input, 0, true); await input.press('Backspace'); await expect(input).toHaveValue('before after'); await input.press('ControlOrMeta+z'); await expect(tokens).toHaveCount(1);
    await besideImage(input, 0, true); await page.keyboard.insertText('X'); await input.press('Shift+Enter'); expect(await plainText(input)).toBe('before X\nafter');
    await fileInput.setInputFiles({ name: 'two.png', mimeType: 'image/png', buffer: bytes }); await expect(tokens).toHaveCount(2);
    await input.evaluate((node) => { const selection = window.getSelection()!; const last = node.lastChild!; selection.setBaseAndExtent(last, last.textContent!.length, node.firstChild!, 0); node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true })); });
    const selectionBefore = await input.evaluate(() => { const selected = window.getSelection()!; return [selected.anchorNode?.textContent, selected.anchorOffset, selected.focusNode?.textContent, selected.focusOffset]; });
    const retained = await input.elementHandle(); const before = connections; await socket!.close({ code: 1012, reason: 'Inline image reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); expect(await input.evaluate((node, previous) => node === previous, retained)).toBe(true); expect(await plainText(input)).toBe('before X\nafter');
    expect(await input.evaluate(() => { const selected = window.getSelection()!; return [selected.anchorNode?.textContent, selected.anchorOffset, selected.focusNode?.textContent, selected.focusOffset]; })).toEqual(selectionBefore);
    await page.getByRole('button', { name: 'Message history' }).click(); await page.getByRole('button', { name: 'Earlier image-test message', exact: true }).click(); await expect(input).toHaveValue('Earlier image-test message'); await page.getByRole('button', { name: 'Restore draft' }).click(); await expect(tokens).toHaveCount(2); expect(await plainText(input)).toBe('before X\nafter');
    await tokens.first().click(); await preview.getByRole('button', { name: 'Remove image' }).click(); await expect(tokens).toHaveCount(1); await input.press('ControlOrMeta+z'); await expect(tokens).toHaveCount(2);
    await page.setViewportSize({ width: 760, height: 720 }); await input.scrollIntoViewIfNeeded(); await expect(input).toBeInViewport(); await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeInViewport(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-inline-images.png') });
    await tokens.first().click(); await expect(preview.getByRole('img')).toBeInViewport(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('composer-image-preview.png') }); await preview.getByRole('button', { name: 'Close dialog' }).click();
    await input.press('Enter'); await expect(page.getByRole('alert')).toContainText('Injected image send refusal'); await expect(tokens).toHaveCount(2);
    expect(sends[0]?.message).toBe(`before ${String(uploads[0]!.path)} X\n${String(uploads[1]!.path)} after`);
    refuseSend = false; await input.press('Enter'); await expect(tokens).toHaveCount(0); await expect(input).toHaveValue(''); expect(sends[1]).toEqual(sends[0]); await input.press('ControlOrMeta+z'); await expect(input).toHaveValue(''); await expect(tokens).toHaveCount(0);
    await page.setViewportSize({ width: 1280, height: 900 }); await input.fill('start finish'); await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(6, 12)); holdUpload = true;
    await fileInput.setInputFiles({ name: 'late.png', mimeType: 'image/png', buffer: bytes }); await expect.poll(() => Boolean(release)).toBe(true); await input.fill('new start finish');
    await page.locator(`[role="treeitem"][data-agent-id="${other}"]`).click(); await page.getByRole('textbox', { name: 'Message Other image worker', exact: true }).fill('Other cell draft'); holdUpload = false; release!(); release = undefined;
    await page.locator(`[role="treeitem"][data-agent-id="${worker}"]`).click(); await expect(tokens).toHaveCount(1); expect(await plainText(input)).toBe('new start ');
    await fileInput.setInputFiles({ name: 'rejected.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') }); await expect(page.getByRole('alert')).toContainText('Only PNG, JPEG, WebP, or GIF'); expect(await plainText(input)).toBe('new start '); await expect(tokens).toHaveCount(1);
    await besideImage(input, 0, true);
    const clipboard = await page.evaluateHandle((png) => { const transfer = new DataTransfer(); transfer.items.add(new File([new Uint8Array(png)], 'pasted.png', { type: 'image/png' })); return transfer; }, [...bytes]);
    await input.evaluate((node, transfer) => node.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer })), clipboard); await expect(tokens).toHaveCount(2); await clipboard.dispose();
    const dropped = await page.evaluateHandle((png) => { const transfer = new DataTransfer(); transfer.items.add(new File([new Uint8Array(png)], 'dropped.png', { type: 'image/png' })); return transfer; }, [...bytes]);
    await input.evaluate((node, transfer) => node.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer })), dropped); await expect(tokens).toHaveCount(3); await dropped.dispose(); expect(await plainText(input)).toBe('new start ');
    await page.locator(`[role="treeitem"][data-agent-id="${other}"]`).click(); await expect(page.getByRole('textbox', { name: 'Message Other image worker', exact: true })).toHaveValue('Other cell draft');
    const imeInput = page.getByRole('textbox', { name: 'Message Other image worker', exact: true }); await imeInput.fill('IME draft '); holdUpload = true;
    await fileInput.setInputFiles({ name: 'ime.png', mimeType: 'image/png', buffer: bytes }); await expect.poll(() => Boolean(release)).toBe(true);
    await imeInput.evaluate((node) => node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })));
    await page.keyboard.insertText('あ'); await imeInput.evaluate((node: HTMLTextAreaElement) => { node.value += 'い'; node.setSelectionRange(node.value.length, node.value.length); });
    const uploadResponse = page.waitForResponse((response) => response.url().endsWith('/api/attachment/upload')); holdUpload = false; release!(); release = undefined; await uploadResponse;
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))); await expect(imeInput).toHaveValue('IME draft あい');
    await imeInput.evaluate((node) => node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: 'あい' }))); await expect(imeInput.locator('[data-composer-image]')).toHaveCount(1); expect(await plainText(imeInput)).toBe('IME draft あい');
    await imeInput.press('ControlOrMeta+z'); await expect(imeInput.locator('[data-composer-image]')).toHaveCount(0); await expect(imeInput).toHaveValue('IME draft あい'); await imeInput.press('ControlOrMeta+z'); await expect(imeInput).toHaveValue('IME draft ');
    await page.locator(`[role="treeitem"][data-agent-id="${String(terminal.id)}"]`).click();
    const terminalInput = page.getByRole('textbox', { name: 'Message Image terminal', exact: true }); await terminalInput.fill('Image: ');
    await fileInput.setInputFiles({ name: 'terminal.png', mimeType: 'image/png', buffer: bytes }); await expect(terminalInput.locator('[data-composer-image]')).toHaveCount(1);
    await terminalInput.press('Enter'); await expect(terminalInput.locator('[data-composer-image]')).toHaveCount(0); await expect(terminalInput).toHaveValue('');
    const terminalMessage = `Image: ${String(uploads.at(-1)!.path)}`; expect(sends.at(-1)).toMatchObject({ cmd: 'send_user_message', cell_id: terminal.id, text: terminalMessage });
    await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Message history' }).click(); await expect(page.getByRole('dialog', { name: 'Recent messages' }).getByRole('button', { name: terminalMessage, exact: true })).toBeVisible();
  } finally { release?.(); for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id }); }
});
