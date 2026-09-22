import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Context panes resize with pointer and keyboard, retry persistence and retain drafts through compact layout and reconnect', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Context split ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  expect(await command(request, { cmd: 'ui_set_context_panel_split', ratio: .36 })).toMatchObject({ type: 'state', context_panel_split_ratio: .36 });
  for (let i = 0; i < 25; i++) await command(request, { cmd: 'memory_publish', scope_kind: 'group', scope_ref: group, entry_type: 'note', title: i === 24 ? 'Resizable note' : `Other note ${i}`, content: `Recorded context ${i}`, source_kind: 'manual' });
  let socket: WebSocketRoute | undefined; let connections = 0; let refusal = false; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'ui_set_context_panel_split') { writes.push(data); if (refusal) { await route.fulfill({ json: { ok: false, error: 'Injected pane width refusal' } }); return; } }
    await route.continue();
  });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Context', exact: true }).click(); };
  await page.goto('/'); await open();
  const separator = page.getByRole('separator', { name: 'Resize Context panes', exact: true }); const list = page.getByRole('complementary', { name: 'Shared context entries', exact: true });
  await expect(separator).toHaveAttribute('aria-valuenow', '36'); await expect(list.getByRole('button')).toHaveCount(25);
  await page.getByRole('button', { name: 'Edit', exact: true }).click(); const content = page.getByRole('textbox', { name: 'Content', exact: true }); await content.fill('Unsaved context draft survives pane resize');
  await content.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(2, 9); node.dataset.resizeAnchor = 'original'; }); await list.evaluate((node) => { node.scrollTop = 180; });
  const split = separator.locator('..'); const bounds = (await split.boundingBox())!; const handle = (await separator.boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, handle.y + 40); await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width * .52, handle.y + 40, { steps: 4 }); expect(writes).toHaveLength(0); await page.mouse.up();
  await expect(separator).toHaveAttribute('aria-valuetext', '52% list width'); expect(writes).toHaveLength(1); expect(Number(writes[0]?.ratio)).toBeCloseTo(.52, 2);
  await expect(content).toBeFocused(); await expect(content).toHaveAttribute('data-resize-anchor', 'original'); await expect(content).toHaveValue('Unsaved context draft survives pane resize'); expect(await content.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 9]); expect(await list.evaluate((node) => node.scrollTop)).toBe(180);
  const listBounds = (await list.boundingBox())!; expect(listBounds.width / (bounds.width - 9)).toBeCloseTo(.52, 2);
  // External persistence and a compact snapshot refresh update width, not the local editor.
  await command(request, { cmd: 'ui_set_context_panel_split', ratio: .44 }); await expect(separator).toHaveAttribute('aria-valuenow', '44');
  const before = connections; await socket!.close({ code: 1012, reason: 'Context split reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(content).toHaveAttribute('data-resize-anchor', 'original'); await expect(content).toHaveValue('Unsaved context draft survives pane resize');
  await page.setViewportSize({ width: 760, height: 640 }); await expect(separator).toHaveCount(0); await content.scrollIntoViewIfNeeded(); await expect(content).toHaveAttribute('data-resize-anchor', 'original'); await expect(content).toHaveValue('Unsaved context draft survives pane resize');
  await page.screenshot({ path: test.info().outputPath('context-split-compact.png') });
  await page.setViewportSize({ width: 1280, height: 720 }); await expect(separator).toHaveAttribute('aria-valuenow', '44');
  refusal = true; await separator.focus(); await separator.press('End'); await expect(page.getByRole('alert')).toContainText('Pane width was not saved'); await expect(separator).toHaveAttribute('aria-valuenow', '62'); await expect(content).toHaveValue('Unsaved context draft survives pane resize');
  refusal = false; await page.getByRole('button', { name: 'Retry pane width', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0); await expect(separator).toHaveAttribute('aria-valuetext', '62% list width');
  await separator.press('Home'); await expect(separator).toHaveAttribute('aria-valuetext', '28% list width'); await separator.press('ArrowRight'); await expect(separator).toHaveAttribute('aria-valuetext', '30% list width');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click(); await page.reload(); await open(); await expect(separator).toHaveAttribute('aria-valuenow', '30');
  await page.screenshot({ path: test.info().outputPath('context-split-restored.png') });
});
