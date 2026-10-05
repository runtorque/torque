import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('structured settings keep nested controls and long reset labels inside their cells at different window widths', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Settings layout ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.goto('/'); await page.getByText('Architect behavior defaults', { exact: true }).click();
  const architect = page.locator('details').filter({ has: page.getByText('Architect behavior defaults', { exact: true }) });
  const ship = architect.getByRole('spinbutton', { name: 'Architect review gate thresholds: Ship direct max', exact: true });
  await ship.fill('72');
  for (const width of [1280, 960, 760]) {
    await page.setViewportSize({ width, height: 720 });
    await expect(ship).toHaveValue('72');
    const overflow = await architect.locator('input, select, textarea, button').evaluateAll((elements) => elements.flatMap((element) => {
      const parent = element.closest('label') ?? element.parentElement;
      if (!parent) return [];
      const box = element.getBoundingClientRect(); const bounds = parent.getBoundingClientRect();
      const escapes = box.left < bounds.left - 1 || box.right > bounds.right + 1 || element.scrollWidth > element.clientWidth + 2;
      return escapes ? [{ name: element.getAttribute('aria-label') || element.textContent, width: box.width, parentWidth: bounds.width, scroll: element.scrollWidth, client: element.clientWidth }] : [];
    }));
    expect(overflow, `Controls must fit their cells at ${width}px`).toEqual([]);
    const threshold = architect.getByRole('group', { name: 'Architect review gate thresholds', exact: true });
    const nestedOverflow = await threshold.evaluate((element) => element.scrollWidth > element.clientWidth + 2);
    expect(nestedOverflow, `Nested threshold fields must fit at ${width}px`).toBe(false);
    await threshold.scrollIntoViewIfNeeded();
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath(`settings-layout-${width}.png`) });
  }
  await architect.getByRole('button', { name: 'Reset Architect review gate thresholds', exact: true }).click();
  await expect(ship).toHaveValue('50');
});
