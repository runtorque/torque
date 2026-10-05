import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Saved dispatch lane and guidance cadence affect actual task placement and delivered prompts', async ({ page, request }) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires a local probe interpreter and isolated daemon');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-dispatch-policy-'))); await mkdir(join(directory, '.torque', 'actions'), { recursive: true });
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url)); const output = join(directory, 'dispatch.jsonl');
  const boot = `${quote(process.env.TORQUE_PTY_PYTHON!)} -u ${quote(probe)} ${quote(output)} dispatch`;
  const group = `Dispatch policy ${Date.now()}`; const name = 'Dispatch policy Worker'; const tasks: string[] = []; const evidence: Row[] = []; let id = '';
  const inputs = async () => { try { return (await readFile(output, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as Row).filter((row) => row.kind === 'input').map((row) => String(row.text)); } catch { return []; } };
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'board_add_lane', name: 'QA Active' });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, default_agent_template: '', agent_provider: 'generic', agent_boot_command: boot, git_worktree: false, notifications: false } });
  await command(request, { cmd: 'save_action', group, scope: 'project', name: 'qa/work', action: { prompt: 'QA_BODY {{ TASK }}\nQA_END {{ TASK }}' } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const setting = async (label: string) => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByLabel(label, { exact: true });
  };
  const cell = async () => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
  try {
    await page.goto('/'); id = String((await command(request, { cmd: 'add_worker', group, name })).id);
    await expect.poll(async () => (await cell()).session_id).toBeTruthy(); const session = (await cell()).session_id;
    const cases = [{ cadence: 2, lane: 'QA Active', hints: [true, true, false, true] }, { cadence: 0, lane: '', hints: [true, true] }, { cadence: 2, lane: 'QA Active', hints: [false] }];
    for (const [phase, config] of cases.entries()) {
      await (await setting('Guidance hint cadence')).fill(String(config.cadence)); await (await setting('Dispatch lane')).fill(config.lane);
      await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); await page.reload();
      await expect(await setting('Guidance hint cadence')).toHaveValue(String(config.cadence)); await expect(await setting('Dispatch lane')).toHaveValue(config.lane || 'In Progress');
      for (const [step, hint] of config.hints.entries()) {
        const title = `Policy ${phase} step ${step}`; const task = String((await command(request, { cmd: 'board_add_task', group, task: title, action_name: 'qa/work', agent_id: id, lane: 'Backlog' })).task_id); tasks.push(task);
        await page.getByRole('button', { name: /▦ Board/ }).click(); await page.getByText(title, { exact: true }).dblclick();
        const dialog = page.getByRole('dialog'); const offset = (await inputs()).length;
        await dialog.getByRole('button', { name: 'Dispatch task', exact: true }).click();
        await expect.poll(async () => (await command(request, { cmd: 'task_detail', id: task })).task).toMatchObject({ lane: config.lane || 'In Progress', dispatch_state: 'live', agent_id: id });
        await expect.poll(async () => (await inputs()).slice(offset).some((line) => line.includes(`QA_END ${title}`))).toBe(true);
        const delivered = (await inputs()).slice(offset); expect(delivered.some((line) => line.startsWith(`You are ${name} (worker, id=${id}).`))).toBe(hint);
        expect((await cell()).session_id).toBe(session); evidence.push({ cadence: config.cadence, lane: config.lane || 'In Progress', task, expectedIdentity: hint, delivered });
        await dialog.getByRole('button', { name: 'Close dialog', exact: true }).click();
        await command(request, { cmd: 'ai_report', cell_id: id, task_id: task, action: 'done', message: 'Local QA prompt received', terminal_declaration: 'No further work is needed; I will not derive after this.' });
      }
    }
    const path = test.info().outputPath('dispatch-policy-evidence.json'); await writeFile(path, JSON.stringify(evidence, null, 2)); await test.info().attach('dispatch-policy-evidence', { path, contentType: 'application/json' });
  } finally {
    if (id) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    for (const task of tasks) await command(request, { cmd: 'board_remove_task', id: task }); await rm(directory, { recursive: true, force: true });
  }
});
