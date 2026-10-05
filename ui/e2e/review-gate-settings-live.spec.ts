import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Architect review thresholds reject lossy drafts and persist typed values after refusal and retry', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Review thresholds ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const original = { ship_direct_max: 50, review_default_above: 150, self_review_bypass_allowed: false };
  await command(request, { cmd: 'update_architect_settings', group, settings: { architect_review_gate_thresholds: original } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.goto('/'); const heading = page.getByText('Architect behavior defaults', { exact: true }); await heading.click();
  const section = page.getByRole('group', { name: 'Architect review gate thresholds', exact: true });
  const ship = section.getByRole('spinbutton', { name: 'Architect review gate thresholds: Ship direct max', exact: true });
  const review = section.getByRole('spinbutton', { name: 'Architect review gate thresholds: Review default above', exact: true });
  await expect(ship).toHaveValue('50'); await expect(review).toHaveValue('150');
  const writes: Row[] = []; let refuse = true;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_architect_settings') { writes.push(data); if (refuse) { await route.fulfill({ json: { ok: false, error: 'Injected threshold refusal' } }); return; } }
    await route.continue();
  });
  const save = page.getByRole('button', { name: 'Save changes', exact: true });
  for (const [input, valid] of [[ship, '0'], [review, '83']] as const) {
    for (const value of ['-1', '1.5', '9007199254740993', '']) {
      await input.fill(value); await heading.click(); await save.click();
      await expect(page.getByRole('alert')).toHaveText('Correct the highlighted setting before saving.'); await expect(input).toBeFocused(); expect(writes).toHaveLength(0);
    }
    await input.fill(valid);
  }
  await section.getByRole('combobox', { name: 'Architect review gate thresholds: Self review bypass allowed', exact: true }).selectOption('true');
  await expect(section.getByRole('button', { name: 'Add entry', exact: true })).toHaveCount(0);
  await save.click(); await expect(page.getByRole('alert')).toContainText('Injected threshold refusal'); await expect(ship).toHaveValue('0'); await expect(review).toHaveValue('83');
  expect(((await command(request, { cmd: 'get_group_settings', group })).architect_settings as Row).architect_review_gate_thresholds).toEqual(original);
  refuse = false; await save.click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  const expected = { ship_direct_max: 0, review_default_above: 83, self_review_bypass_allowed: true };
  expect(writes).toHaveLength(2); expect(writes[1]).toEqual({ cmd: 'update_architect_settings', group, settings: { architect_review_gate_thresholds: expected } });
  const saved = await command(request, { cmd: 'get_group_settings', group });
  expect((saved.architect_settings as Row).architect_review_gate_thresholds).toEqual(expected); expect((saved.settings as Row).architect_review_gate_thresholds).toEqual(expected);
  await page.reload(); await heading.click(); await expect(ship).toHaveValue('0'); await expect(review).toHaveValue('83');
  await expect(section.getByRole('combobox')).toHaveValue('true'); await section.scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('architect-review-thresholds.png') });
});
