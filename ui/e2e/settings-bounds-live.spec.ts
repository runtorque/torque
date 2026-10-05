import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
const fields = [
  ['group', 'guidance_hint_cadence', 'Guidance hint cadence', 0, 100],
  ['group', 'context_default_ttl_days', 'Context default ttl days', 1, 60],
  ['global', 'perceived_empty_probe_threshold', 'Perceived empty probe threshold', 2, 25],
  ['global', 'perceived_empty_window_seconds', 'Perceived empty window seconds', 10, 3600],
] as const;
test('settings reject silent numeric coercion and round-trip every supported boundary', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = `Bounded settings ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const writes: Row[] = [];
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data); await route.continue(); });
  const openSettings = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const reveal = async (label: string) => { await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByRole('spinbutton', { name: label, exact: true }); };
  const save = page.getByRole('button', { name: 'Save changes', exact: true });
  try {
    await command(request, { cmd: 'update_global_settings', settings: { perceived_empty_probe_threshold: 5, perceived_empty_window_seconds: 120 } });
    await page.goto('/'); await openSettings();
    for (const [, , label, minimum, maximum] of fields) {
      const input = await reveal(label);
      for (const value of ['', String(minimum - 1), String(maximum + 1), String(minimum + 0.5)]) {
        await input.fill(value);
        await input.locator('xpath=ancestor::details[1]').locator('summary').first().click();
        await save.click(); await expect(page.getByRole('alert')).toHaveText('Correct the highlighted setting before saving.');
        await expect(input).toBeFocused(); await expect(input).toHaveValue(value); expect(writes).toHaveLength(0);
      }
      await input.fill(String(minimum));
    }
    await page.screenshot({ path: test.info().outputPath('settings-bounds.png') });
    for (const boundary of ['minimum', 'maximum'] as const) {
      if (boundary === 'maximum') for (const [, , label, , maximum] of fields) await (await reveal(label)).fill(String(maximum));
      await save.click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
      const global = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
      const grouped = (await command(request, { cmd: 'get_group_settings', group })).settings as Row;
      for (const [scope, key, , minimum, maximum] of fields) expect((scope === 'global' ? global : grouped)[key]).toBe(boundary === 'minimum' ? minimum : maximum);
      await page.reload(); await openSettings();
      for (const [, , label, minimum, maximum] of fields) await expect(page.getByLabel(label, { exact: true })).toHaveValue(String(boundary === 'minimum' ? minimum : maximum));
    }
    expect(writes.map((write) => write.cmd)).toEqual(['update_global_settings', 'update_group_settings', 'update_global_settings', 'update_group_settings']);
  } finally {
    await command(request, { cmd: 'update_global_settings', settings: { perceived_empty_probe_threshold: original.perceived_empty_probe_threshold, perceived_empty_window_seconds: original.perceived_empty_window_seconds } });
  }
});
