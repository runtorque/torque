import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Settings previews unsaved Engineer and Architect drafts, validates replies and copies only current output', async ({ page, request, context }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Prompt preview ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', worker_provider: 'generic' } });
  const before = await command(request, { cmd: 'get_group_settings', group });
  const previews: Row[] = []; const writes: Row[] = []; let mismatch = false;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['update_group_settings', 'engineer_update_settings', 'update_architect_settings', 'update_global_settings'].includes(String(data.cmd))) writes.push(data);
    if (data.cmd === 'preview_system_prompt') {
      previews.push(data);
      if (mismatch) { await route.fulfill({ json: { ok: true, data: { type: 'system_prompt_preview', request_id: 'obsolete', group, kind: data.kind, prompt: 'Wrong response' } } }); return; }
    }
    await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const directory = page.getByRole('textbox', { name: 'Default directory', exact: true }); await directory.fill('/private/tmp/unsaved-preview');
  await page.getByText('Engineer behavior defaults', { exact: true }).click();
  const instructions = page.getByRole('textbox', { name: 'Custom instructions', exact: true }); await instructions.fill('Engineer preview sentinel: preserve this unsaved instruction.');
  const region = page.getByRole('region', { name: 'System prompt preview' });
  await region.getByRole('button', { name: 'Preview Engineer system prompt', exact: true }).click();
  const output = region.getByLabel('Rendered system prompt'); await expect(output).toContainText('Engineer preview sentinel');
  expect(previews.at(-1)).toMatchObject({ group, kind: 'engineer', group_settings: { default_directory: '/private/tmp/unsaved-preview' }, settings: { custom_instructions: 'Engineer preview sentinel: preserve this unsaved instruction.' } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await region.getByRole('button', { name: 'Copy rendered prompt', exact: true }).click(); await expect(region.getByText('Prompt copied.', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await output.textContent());
  await instructions.fill('Engineer revised unsaved sentinel'); await expect(region.getByRole('status')).toContainText('Draft changed'); await expect(region.getByRole('button', { name: 'Copy rendered prompt', exact: true })).toBeDisabled();
  mismatch = true; await region.getByRole('button', { name: 'Preview Engineer system prompt', exact: true }).click(); await expect(region.getByRole('alert')).toContainText('did not match'); await expect(region.getByText('Wrong response', { exact: true })).toHaveCount(0);
  mismatch = false; await region.getByRole('button', { name: 'Retry prompt preview', exact: true }).click(); await expect(output).toContainText('Engineer revised unsaved sentinel');
  await page.getByText('Architect behavior defaults', { exact: true }).click();
  await page.getByRole('textbox', { name: 'Architect custom instructions', exact: true }).fill('Architect preview sentinel: preserve the unsaved plan.');
  await region.getByRole('button', { name: 'Preview Architect system prompt', exact: true }).click(); await expect(output).toContainText('Architect preview sentinel');
  expect(previews.at(-1)).toMatchObject({ group, kind: 'architect', settings: { architect_custom_instructions: 'Architect preview sentinel: preserve the unsaved plan.' } });
  await expect(directory).toHaveValue('/private/tmp/unsaved-preview'); await expect(instructions).toHaveValue('Engineer revised unsaved sentinel'); expect(writes).toEqual([]);
  const after = await command(request, { cmd: 'get_group_settings', group }); expect(after.settings).toEqual(before.settings); expect(after.engineer_settings).toEqual(before.engineer_settings); expect(after.architect_settings).toEqual(before.architect_settings);
  await region.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('settings-prompt-preview.png') });
  await page.setViewportSize({ width: 390, height: 844 }); await region.scrollIntoViewIfNeeded();
  expect(await region.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true); await page.screenshot({ path: test.info().outputPath('settings-prompt-preview-narrow.png') });
});
