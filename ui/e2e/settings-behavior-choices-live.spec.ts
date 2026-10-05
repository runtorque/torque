import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
type Choice = string | number | boolean;
interface Field { key: string; label: string; choices: Choice[]; policy?: { prefix: string; labels: string[] } }
// Independent Classic control contract. Do not derive expected choices from the React renderer.
const engineer: Field[] = [
  { key: 'autonomy_mode', label: 'Autonomy mode', choices: ['suggest_only', 'dispatch_when_clear', 'aggressive_auto_continue'], policy: { prefix: 'Autonomy mode', labels: ['Suggest only', 'Dispatch when clear', 'Aggressive auto-continue'] } },
  { key: 'wave_size_preference', label: 'Wave size preference', choices: ['small', 'balanced', 'large'], policy: { prefix: 'Wave size preference', labels: ['Small reviewable waves', 'Balanced waves', 'Fill available capacity'] } },
  { key: 'same_agent_follow_up_preference', label: 'Same agent follow up preference', choices: ['balanced', 'prefer_same_agent', 'prefer_fresh_agent'], policy: { prefix: 'Same-agent follow-up preference', labels: ['Balanced', 'Prefer same agent', 'Prefer fresh agent'] } },
  { key: 'digest_verbosity', label: 'Digest verbosity', choices: ['compact', 'balanced', 'detailed'], policy: { prefix: 'Digest verbosity', labels: ['Compact', 'Balanced', 'Detailed'] } },
  { key: 'escalation_style', label: 'Escalation style', choices: ['ask_early', 'note_then_ask', 'keep_moving'], policy: { prefix: 'Escalation style', labels: ['Ask early', 'Note first, ask when blocked', 'Keep moving unless blocked'] } },
  { key: 'default_worker_concurrency', label: 'Default worker concurrency', choices: [1, 2, 3, 4, 5, 6, 8], policy: { prefix: 'Default worker concurrency', labels: ['1', '2', '3', '4', '5', '6', '8'] } },
  { key: 'push_interval', label: 'Push interval', choices: [10, 30, 60, 120, 300] },
  { key: 'max_interval', label: 'Max interval', choices: [60, 120, 300, 600] },
  { key: 'heartbeat_interval', label: 'Heartbeat interval', choices: [0, 60, 120, 300, 600] },
  { key: 'paused', label: 'Paused', choices: [true, false] },
  { key: 'restrict_to_created_agents', label: 'Restrict to created agents', choices: [true, false], policy: { prefix: 'Owned-agent restriction', labels: ['Enabled', 'Disabled'] } },
  { key: 'engineer_can_override_worker_provider', label: 'Engineer can override worker provider', choices: [true, false] },
];
const architect: Field[] = [
  { key: 'architect_autonomy_mode', label: 'Architect autonomy mode', choices: ['dispatch_freely', 'dispatch_after_confirm', 'ask_always'], policy: { prefix: 'Autonomy mode', labels: ['Dispatch freely', 'Dispatch after confirm', 'Ask always'] } },
  { key: 'architect_digest_verbosity', label: 'Architect digest verbosity', choices: ['terse', 'balanced', 'verbose'] },
  { key: 'architect_push_interval', label: 'Architect push interval', choices: [60, 120, 300, 600, 900] },
  { key: 'architect_max_interval', label: 'Architect max interval', choices: [120, 300, 600, 1200, 1800] },
  { key: 'architect_heartbeat_interval', label: 'Architect heartbeat interval', choices: [0, 300, 600, 1200, 1800] },
  { key: 'architect_suppress_empty_digests', label: 'Architect suppress empty digests', choices: [true, false] },
];
async function command(request: APIRequestContext, data: Row) {
  const response = await request.post('/api/cmd', { data });
  expect(response.ok()).toBe(true);
  const result = await response.json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
for (const [kind, fields] of [['engineer', engineer], ['architect', architect]] as const) {
  test(`${kind} behavior choices survive reconnect, save and reopen and reach the real prompt policy`, async ({ page, request }) => {
    test.setTimeout(180_000);
    const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
    expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
    const group = `${kind} behavior acceptance ${Date.now()}`;
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', worker_provider: 'generic' } });
    const frameKey = `${kind}_settings`;
    const baseline = (await command(request, { cmd: 'get_group_settings', group }))[frameKey] as Row;
    const title = kind === 'engineer' ? 'Engineer' : 'Architect';
    const writeCommand = kind === 'engineer' ? 'engineer_update_settings' : 'update_architect_settings';
    const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0; let refreshed = 0; let unrelatedUpdates = 0;
    await page.routeWebSocket(/\/ws\?/, (client) => {
      socket = client; connections++; const server = client.connectToServer();
      server.onMessage((message) => {
        const frame = JSON.parse(String(message)) as Row;
        if (frame.type === 'delta' && Array.isArray(frame.ops)) unrelatedUpdates += (frame.ops as Row[]).filter((op) => op.op === 'group_settings_update' && op.name === group).length;
        client.send(message);
      });
    });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === writeCommand) writes.push(data);
      if (data.cmd === 'get_group_settings') refreshed++;
      await route.continue();
    });
    const open = async () => {
      await page.getByRole('button', { name: /◎ Control/ }).click();
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      const summary = page.getByText(`${title} behavior defaults`, { exact: true });
      if (!(await summary.evaluate((element) => element.closest('details')?.open))) await summary.click();
    };
    const picker = (field: Field) => page.getByRole('combobox', { name: field.label, exact: true });
    await page.goto('/'); await open();
    for (const field of fields) {
      await expect(picker(field)).toHaveValue(String(baseline[field.key]));
      expect(await picker(field).locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))).toEqual(field.choices.map(String));
    }
    const evidence: Row[] = [];
    const rounds = Math.max(...fields.map((field) => field.choices.length));
    let previous = baseline;
    for (let round = 0; round < rounds; round++) {
      const expected = Object.fromEntries(fields.map((field) => [field.key, field.choices[round % field.choices.length]]));
      for (const [index, field] of fields.entries()) {
        const control = picker(field); await control.selectOption(String(expected[field.key]));
        if (round === 0) {
          await control.focus(); await control.evaluate((element) => element.setAttribute('data-retained', 'yes'));
          const updatesBefore = unrelatedUpdates;
          await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: index + 1 } });
          await expect.poll(() => unrelatedUpdates).toBeGreaterThan(updatesBefore);
          await expect(control).toBeFocused(); await expect(control).toHaveAttribute('data-retained', 'yes');
          await expect(control).toHaveValue(String(expected[field.key]));
        }
      }
      const focus = picker(fields[0]!); await focus.focus(); await focus.evaluate((element) => element.setAttribute('data-retained', 'yes'));
      const priorConnections = connections; const priorRefreshes = refreshed; const priorWrites = writes.length;
      await socket!.close({ code: 1012, reason: 'Behavior defaults draft reconnect' });
      await expect.poll(() => connections).toBeGreaterThan(priorConnections);
      await expect.poll(() => refreshed).toBeGreaterThan(priorRefreshes);
      await expect(focus).toBeFocused(); await expect(focus).toHaveAttribute('data-retained', 'yes');
      for (const field of fields) await expect(picker(field)).toHaveValue(String(expected[field.key]));
      expect(writes).toHaveLength(priorWrites);
      await page.getByRole('button', { name: 'Save changes', exact: true }).click();
      await expect(page.getByText('Saved', { exact: true })).toBeVisible();
      expect(writes).toHaveLength(priorWrites + 1);
      const write = writes.at(-1)!;
      const delta = Object.fromEntries(Object.entries(expected).filter(([key, value]) => previous[key] !== value));
      expect(write).toEqual(kind === 'engineer' ? { cmd: writeCommand, group, ...delta } : { cmd: writeCommand, group, settings: delta });
      const stored = (await command(request, { cmd: 'get_group_settings', group }))[frameKey] as Row;
      expect(stored).toEqual({ ...previous, ...expected });
      // No draft overrides: the daemon must build from the settings actually saved above.
      const preview = await command(request, { cmd: 'preview_system_prompt', group, kind });
      const policyLines = fields.flatMap((field) => field.policy ? [`${field.policy.prefix}: ${field.policy.labels[round % field.choices.length]}`] : []);
      for (const line of policyLines) expect(preview.prompt).toContain(line);
      evidence.push({ round, expected, stored, policyLines }); previous = stored;
      await page.reload(); await open();
      for (const field of fields) await expect(picker(field)).toHaveValue(String(expected[field.key]));
    }
    const region = page.getByRole('region', { name: 'System prompt preview' });
    await region.getByRole('button', { name: `Preview ${title} system prompt`, exact: true }).click();
    for (const line of evidence.at(-1)!.policyLines as string[]) await expect(region.getByLabel('Rendered system prompt')).toContainText(line);
    await region.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath(`${kind}-policy.png`) });
    // Every control has a daemon-backed reset, including Off heartbeat and booleans.
    for (const field of fields) {
      const reset = page.getByRole('button', { name: `Reset ${field.label}`, exact: true });
      if (await reset.isEnabled()) await reset.click();
      await expect(picker(field)).toHaveValue(String(baseline[field.key]));
    }
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect((await command(request, { cmd: 'get_group_settings', group }))[frameKey]).toEqual(baseline);
    await page.reload(); await open();
    for (const field of fields) await expect(picker(field)).toHaveValue(String(baseline[field.key]));
    const evidencePath = test.info().outputPath(`${kind}-roundtrips.json`);
    await writeFile(evidencePath, JSON.stringify(evidence, null, 2));
    await test.info().attach('behavior-roundtrips', { path: evidencePath, contentType: 'application/json' });
  });
}
