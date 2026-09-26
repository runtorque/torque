import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
for (const phase of ['request', 'body'] as const) test(`Planning hire notes and journal entries survive reconnect and a real rejection ${phase} deadline`, async ({ page, request }) => {
  test.setTimeout(65_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Planning team ${phase} ${Date.now()}`; const agents: string[] = []; const writes: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; let refuse = true;
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } });
    for (const kind of ['architect', 'engineer']) agents.push(String((await command(request, { cmd: `add_${kind}`, group, name: `Team QA ${kind} ${phase}`, command: '/bin/cat', provider: 'generic', directory: '/private/tmp' })).id));
    const hire = await command(request, { cmd: 'architect_engineer_hire', architect_id: agents[0], name: 'Requested specialty', command: '/bin/cat', provider: 'generic', directory: '/private/tmp' });
    const journalBody = 'Completed the parity review.\nEvidence remains readable on the second line.\nA third line is preserved.';
    await command(request, { cmd: 'engineer_journal_append', group, author_cell_id: agents[1], entry_type: 'checkpoint', entry: journalBody });
    await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'planning', controlTab: 'mission' } });
    await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'pending_hire_reject') {
        writes.push(data);
        if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'Injected hire refusal' } }); return; }
      }
      await route.continue();
    });
    await page.addInitScript(() => {
      const target = window as Window & { qaHoldHire?: 'request' | 'body'; qaHireHeld?: boolean; qaReleaseHire?: () => Promise<void> };
      const original = window.fetch.bind(window);
      window.fetch = async (input, options) => {
        const data = typeof options?.body === 'string' ? JSON.parse(options.body) as Record<string, unknown> : {};
        if (data.cmd !== 'pending_hire_reject' || !target.qaHoldHire) return original(input, options);
        const phase = target.qaHoldHire; delete target.qaHoldHire;
        const response = await original(input, { ...options, signal: null });
        if (phase === 'request') return new Promise<Response>((resolve) => { target.qaHireHeld = true; target.qaReleaseHire = () => { resolve(response); return Promise.resolve(); }; });
        const parse = response.json.bind(response);
        response.json = () => new Promise((resolve, reject) => { target.qaHireHeld = true; target.qaReleaseHire = async () => { try { resolve(await parse()); } catch (cause) { reject(cause instanceof Error ? cause : new Error('Hire body fixture failed')); } }; });
        return response;
      };
    });
    await page.goto('/'); await page.getByRole('button', { name: 'Hires & journals', exact: true }).click();
    const summary = page.locator('summary').filter({ hasText: 'Completed the parity review.' });
    await expect(summary).toContainText('Team QA engineer'); await expect(summary).toContainText('checkpoint');
    await summary.click(); const detail = summary.locator('..'); await expect(detail.locator('p')).toHaveText(journalBody);
    const note = 'Please choose a different specialty.\nRetain this reviewed explanation.';
    await page.getByRole('button', { name: 'Reject with note', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Reject hire request' }); const input = dialog.getByRole('textbox', { name: 'Optional note' });
    await expect(dialog).toContainText('Requested specialty'); await expect(dialog).toContainText('Team QA architect');
    await input.fill(note); await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 8));
    const before = connections; await socket!.close({ code: 1012, reason: 'Hire rejection draft' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(page.getByText('Refreshing Planning…', { exact: true })).toHaveCount(0);
    await expect(input).toHaveValue(note); await expect(input).toBeFocused(); expect(await input.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await expect(detail).toHaveAttribute('open', ''); expect(writes).toHaveLength(0);
    await dialog.getByRole('button', { name: 'Reject hire', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('Injected hire refusal'); await expect(input).toHaveValue(note);
    await page.evaluate((phase) => { (window as Window & { qaHoldHire?: string }).qaHoldHire = phase; }, phase);
    await dialog.getByRole('button', { name: 'Retry rejection', exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as Window & { qaHireHeld?: boolean }).qaHireHeld)).toBe(true);
    await expect(dialog.getByRole('button', { name: 'Rejecting…', exact: true })).toBeDisabled();
    await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 });
    const stored = (await command(request, { cmd: 'pending_hire_list', architect_id: agents[0] })).pending_hires as Row[];
    expect(stored.find((row) => row.id === hire.hire_id)).toMatchObject({ status: 'rejected', resolution_note: note });
    const previous = connections; await socket!.close({ code: 1012, reason: 'Resolved hire awaiting acknowledgement' }); await expect.poll(() => connections).toBeGreaterThan(previous);
    await expect(input).toHaveValue(note); await expect(input).toHaveAttribute('readonly', ''); expect(writes).toHaveLength(2);
    await page.screenshot({ path: test.info().outputPath(`planning-team-${phase}-recovery.png`) });
    await dialog.getByRole('button', { name: 'Retry rejection', exact: true }).click(); await expect(dialog).toHaveCount(0);
    await page.evaluate(async () => { await (window as Window & { qaReleaseHire?: () => Promise<void> }).qaReleaseHire?.(); });
    await expect(page.getByRole('button', { name: 'Reject with note', exact: true })).toHaveCount(0);
    expect(writes).toEqual(Array.from({ length: 3 }, () => ({ cmd: 'pending_hire_reject', id: hire.hire_id, note })));
    await expect(detail).toHaveAttribute('open', ''); await expect(detail.locator('p')).toHaveText(journalBody);
    await page.reload(); await page.getByRole('button', { name: 'Hires & journals', exact: true }).click(); await summary.click(); await expect(detail.locator('p')).toHaveText(journalBody);
    await page.screenshot({ path: test.info().outputPath(`planning-journal-${phase}.png`) });
    const final = (await command(request, { cmd: 'pending_hire_list', architect_id: agents[0] })).pending_hires as Row[];
    expect(final.find((row) => row.id === hire.hire_id)).toMatchObject({ status: 'rejected', resolution_note: note });
  } finally {
    await page.evaluate(async () => { await (window as Window & { qaReleaseHire?: () => Promise<void> }).qaReleaseHire?.(); }).catch(() => undefined);
    for (const id of agents.reverse()) await command(request, { cmd: 'remove_agent', id });
  }
});
