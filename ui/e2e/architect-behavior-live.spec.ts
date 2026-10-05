import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
test('Architect behavior review honors direct, operator-gated and rejected routes through actual UI decisions', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Architect behavior ${Date.now()}`; const writes: Row[] = []; const evidence: Row[] = []; let refused = false; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (['behavior_overlay_architect_approve', 'behavior_overlay_architect_reject', 'behavior_overlay_user_approve'].includes(String(data.cmd))) { writes.push(data); if (!refused) { refused = true; await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Local Architect review refusal' } } }); return; } } await route.continue(); });
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false } });
    const architect = String((await command(request, { cmd: 'add_architect', group, name: 'Review Architect' })).id);
    const engineer = String((await command(request, { cmd: 'add_engineer', group, name: 'Review Engineer', hired_by_architect_id: architect })).id);
    const read = () => command(request, { cmd: 'behavior_overlay_read', group, agent_id: engineer, seed: true });
    const region = page.getByRole('region', { name: 'Dynamic Behavior', exact: true });
    for (const mode of ['direct', 'operator', 'reject']) {
      const required = mode !== 'direct'; await command(request, { cmd: 'update_group_settings', group, settings: { engineer_behavior_requires_user_approval: required } }); const before = await read(); const instructions = `Reviewed ${mode} behavior`; const rationale = `Review ${mode} route`;
      const created = await command(request, { cmd: 'behavior_overlay_propose', group, agent_id: engineer, proposed_by_agent_id: engineer, proposed_by_kind: 'engineer', text: instructions, rationale }); const id = String(created.proposal_id);
      expect(created.next_actor_kind).toBe('architect');
      await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'catalog' } }); await page.goto('/');
      await region.getByRole('combobox', { name: 'Behavior scope', exact: true }).selectOption('agent'); await region.getByRole('combobox', { name: 'Behavior target', exact: true }).selectOption(engineer);
      const card = region.locator('article').filter({ hasText: rationale }); await card.getByRole('button', { name: 'Review behavior diff', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Review behavior diff', exact: true }); await expect(dialog.getByLabel('Behavior diff', { exact: true })).toContainText(`+${instructions}`);
      const approve = dialog.getByRole('button', { name: 'Approve behavior change', exact: true }); await expect(approve).toBeDisabled(); await dialog.getByRole('combobox', { name: 'Acting Architect', exact: true }).selectOption(architect); const note = dialog.getByRole('textbox', { name: 'Review note', exact: true }); await note.fill(`Reviewed ${mode} note`);
      if (mode === 'direct') {
        await approve.click(); await expect(dialog.getByRole('alert')).toContainText('Local Architect review refusal'); await expect(note).toHaveValue('Reviewed direct note'); expect((await read()).text).toBe(before.text); const previous = writes.length;
        const connected = connections; await socket!.close({ code: 1012, reason: 'Architect review reconnect' }); await expect.poll(() => connections).toBeGreaterThan(connected); await expect(approve).toBeEnabled(); expect(writes).toHaveLength(previous); await expect(dialog.getByRole('combobox', { name: 'Acting Architect', exact: true })).toHaveValue(architect); await expect(note).toHaveValue('Reviewed direct note');
      }
      await (mode === 'reject' ? dialog.getByRole('button', { name: 'Reject behavior change', exact: true }) : approve).click();
      const expected = mode === 'operator' ? 'Architect approval recorded. Operator approval is still required.' : mode === 'reject' ? 'Behavior change rejected.' : 'Behavior change approved.';
      await expect(dialog.getByText(expected, { exact: true })).toBeVisible();
      const proposal = ((await command(request, { cmd: 'behavior_overlay_proposals', group, agent_id: engineer })).proposals as Row[]).find((row) => row.id === id)!;
      expect(proposal.status).toBe(mode === 'operator' ? 'approved' : mode === 'reject' ? 'rejected' : 'applied'); expect((await read()).text).toBe(mode === 'direct' ? instructions : before.text);
      expect(writes.at(-1)).toMatchObject({ cmd: mode === 'reject' ? 'behavior_overlay_architect_reject' : 'behavior_overlay_architect_approve', architect_id: architect, proposal_id: id, expected_proposed_text_sha256: proposal.proposed_text_sha256, note: `Reviewed ${mode} note` });
      if (mode === 'operator') await page.screenshot({ path: test.info().outputPath('architect-awaiting-operator.png'), animations: 'disabled' });
      await dialog.getByRole('button', { name: 'Close review', exact: true }).click();
      if (mode === 'operator') { await card.getByRole('button', { name: 'Review behavior diff', exact: true }).click(); await expect(dialog.getByRole('combobox', { name: 'Acting Architect', exact: true })).toHaveCount(0); await approve.click(); await expect(dialog.getByText('Behavior change approved.', { exact: true })).toBeVisible(); await dialog.getByRole('button', { name: 'Close review', exact: true }).click(); expect((await read()).text).toBe(instructions); }
      await page.reload(); await region.getByRole('combobox', { name: 'Behavior scope', exact: true }).selectOption('agent'); await region.getByRole('combobox', { name: 'Behavior target', exact: true }).selectOption(engineer); await expect(region.getByRole('textbox', { name: 'Behavior instructions', exact: true })).toHaveValue(mode === 'reject' ? String(before.text) : instructions); evidence.push({ mode, proposal, current: await read() });
    }
    await writeFile(test.info().outputPath('architect-behavior.json'), JSON.stringify({ writes, evidence }, null, 2));
  } finally { await command(request, { cmd: 'remove_group', group }); }
});
