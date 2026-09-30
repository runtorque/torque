import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row, allowError = false): Promise<Row> {
  const response = await request.post('/api/cmd', { data }); expect(response.ok()).toBe(true);
  const result = await response.json() as { ok: boolean; error?: string; data: Row };
  if (allowError) { expect(result.ok).toBe(false); return result; }
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function setup(page: Page, request: APIRequestContext, prefix: string) {
  page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `${prefix} ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false, agent_idle_timeout: 0 } });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.goto('/'); return group;
}
async function setting(page: Page, label: string) {
  await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click();
  return page.getByLabel(label, { exact: true });
}
async function save(page: Page) {
  await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
}
async function evidence(name: string, value: unknown) {
  const path = test.info().outputPath(`${name}.json`); await writeFile(path, JSON.stringify(value, null, 2)); await test.info().attach(name, { path, contentType: 'application/json' });
}

test('Event retention immediately trims persisted history and later growth never resurrects removed events', async ({ page, request }) => {
  test.setTimeout(90_000);
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = await setup(page, request, 'Event retention'); let id = '';
  const events = async () => (await command(request, { cmd: 'get_events', limit: 200 })).events as Row[];
  try {
    await (await setting(page, 'Event retention')).fill('100'); await save(page); await page.reload(); await expect(await setting(page, 'Event retention')).toHaveValue('100');
    id = String((await command(request, { cmd: 'add_worker', group, name: 'Retention Worker' })).id);
    const report = async (index: number) => command(request, { cmd: 'ai_report', cell_id: id, action: 'blocked', message: `Retention sample ${index}` });
    for (let index = 0; index < 70; index++) await report(index);
    await expect.poll(async () => (await events()).filter((event) => event.cell_id === id && event.kind === 'agent_blocked').length).toBe(70);
    const before = await events(); const retained = before.slice(-50);
    await (await setting(page, 'Event retention')).fill('50'); await save(page);
    await expect.poll(async () => (await events()).map((event) => event.id)).toEqual(retained.map((event) => event.id));
    await page.reload(); await expect(await setting(page, 'Event retention')).toHaveValue('50');
    await (await setting(page, 'Event retention')).fill('100'); await save(page); await report(70);
    await expect.poll(async () => (await events()).at(-1)?.message).toBe('Retention sample 70');
    const after = await events(); expect(after.slice(0, -1)).toEqual(retained); expect(after).toHaveLength(51);
    await page.reload(); await expect(await setting(page, 'Event retention')).toHaveValue('100');
    await evidence('event-retention', { before, retained, after });
  } finally {
    if (id) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    await command(request, { cmd: 'update_global_settings', settings: { max_event_log: original.max_event_log } });
  }
});

test('Saved pipeline depth rejects over-limit derivation and zero allows the same continuation', async ({ page, request }) => {
  test.setTimeout(90_000);
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = await setup(page, request, 'Pipeline depth'); const tasks: string[] = []; let id = '';
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-depth-policy-'))); await mkdir(join(directory, '.torque', 'actions'), { recursive: true });
  try {
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory } });
    await command(request, { cmd: 'save_action', group, scope: 'project', name: 'qa/depth', action: { prompt: 'Depth QA {{ TASK }}', transitions: [{ action: 'qa/depth', target: 'self' }] } });
    id = String((await command(request, { cmd: 'add_worker', group, name: 'Depth Worker' })).id);
    const root = String((await command(request, { cmd: 'board_add_task', group, task: 'Depth root', action_name: 'qa/depth' })).task_id); tasks.push(root);
    const parent = String((await command(request, { cmd: 'board_add_task', group, task: 'Depth one', action_name: 'qa/depth', parent_task_id: root, pipeline_root_id: root, pipeline_depth: 1 })).task_id); tasks.push(parent);
    await command(request, { cmd: 'dispatch_task', id: parent, agent_id: id });
    await (await setting(page, 'Pipeline depth')).fill('1'); await save(page); await page.reload(); await expect(await setting(page, 'Pipeline depth')).toHaveValue('1');
    const derive = { cmd: 'ai_report', cell_id: id, task_id: parent, action: 'derive', action_name: 'qa/depth', message: 'Depth two continuation' };
    const denied = await command(request, derive, true); expect(denied).toMatchObject({ ok: false, error: 'Pipeline depth limit (1) reached' });
    const deniedTask = (await command(request, { cmd: 'task_detail', id: parent })).task as Row; expect(deniedTask.labels).toContain('torque:depth-limit');
    await (await setting(page, 'Pipeline depth')).fill('0'); await save(page); await page.reload(); await expect(await setting(page, 'Pipeline depth')).toHaveValue('0');
    const accepted = await command(request, derive); expect(accepted.type).toBe('ok'); tasks.push(String(accepted.task_id));
    const child = (await command(request, { cmd: 'task_detail', id: accepted.task_id })).task as Row;
    expect(child).toMatchObject({ pipeline_depth: 2, parent_task_id: parent, pipeline_root_id: root, agent_id: id, dispatch_state: 'live' });
    await evidence('pipeline-depth', { denied, deniedTask, accepted, child });
  } finally {
    if (id) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    for (const task of tasks.reverse()) await command(request, { cmd: 'board_remove_task', id: task });
    await command(request, { cmd: 'update_global_settings', settings: { max_pipeline_depth: original.max_pipeline_depth } }); await rm(directory, { recursive: true, force: true });
  }
});

test('Saved repeated-probe threshold and observation window govern actual ingested episodes', async ({ page, request }) => {
  test.setTimeout(120_000);
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const episodes: Row[] = []; const ids: string[] = []; const results: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (client) => {
    const server = client.connectToServer(); server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'delta' && Array.isArray(frame.ops)) episodes.push(...(frame.ops as Row[]).filter((op) => op.op === 'perceived_empty_episode'));
      client.send(message);
    });
  });
  const group = await setup(page, request, 'Probe detection');
  try {
    for (const config of [{ threshold: 3, window: 60, gap: false }, { threshold: 2, window: 60, gap: true }, { threshold: 2, window: 10, gap: true }]) {
      await (await setting(page, 'Perceived empty probe threshold')).fill(String(config.threshold)); await (await setting(page, 'Perceived empty window seconds')).fill(String(config.window)); await save(page); await page.reload();
      await expect(await setting(page, 'Perceived empty probe threshold')).toHaveValue(String(config.threshold)); await expect(await setting(page, 'Perceived empty window seconds')).toHaveValue(String(config.window));
      const id = String((await command(request, { cmd: 'add_worker', group, name: `Probes ${config.threshold}/${config.window}` })).id); ids.push(id);
      const observed = () => episodes.filter((episode) => episode.cell_id === id);
      const ingest = async (index: number) => {
        const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': id }, data: { event_id: `${id}-${index}`, hook_event_name: 'PostToolUse', tool_name: 'Bash', tool_input: { command: 'echo QA_PROBE' }, tool_response: { stdout: 'QA_PROBE\n' } } }); expect(response.status()).toBe(200);
      };
      await ingest(0); expect(observed()).toHaveLength(0);
      if (config.gap) { const start = Date.now(); await expect.poll(() => Date.now() - start, { timeout: 15_000, intervals: [500] }).toBeGreaterThan(11_000); }
      await ingest(1);
      if (config.threshold === 3 || config.window === 10) {
        // HTTP ingest synchronously evaluates the detector before returning.
        // A read request also provides a transport barrier before the negative assertion.
        expect(((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!.needs_attention).toBe(false);
        expect(observed()).toHaveLength(0); await ingest(2);
      }
      await expect.poll(() => observed().length).toBe(1);
      const episode = observed()[0]!.episode as Row;
      expect(episode).toMatchObject({ threshold_n: config.threshold, window_seconds: config.window, confidence: 'high' });
      expect(episode.tool_calls).toHaveLength(config.threshold);
      await expect.poll(async () => ((await command(request, { cmd: 'get_events', limit: 200 })).events as Row[]).some((event) => event.kind === 'perceived_empty_episode' && event.cell_id === id)).toBe(true);
      results.push({ config, episode });
    }
    await evidence('probe-detection', results);
  } finally {
    for (const id of ids.reverse()) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    await command(request, { cmd: 'update_global_settings', settings: { perceived_empty_probe_threshold: original.perceived_empty_probe_threshold, perceived_empty_window_seconds: original.perceived_empty_window_seconds } });
  }
});
