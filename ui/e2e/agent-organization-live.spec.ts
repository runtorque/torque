import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function organize(page: Page, name: string) {
  await page.getByRole('button', { name: `Actions for ${name}`, exact: true }).click();
  await page.getByRole('menuitem', { name: 'Move or reorder…', exact: true }).click();
  return page.getByRole('dialog', { name: 'Move or reorder agent', exact: true });
}

test('agent ordering and terminal reparenting preserve real sessions, ownership and saved group/child order', async ({ page, request }) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-organization-')));
  await mkdir(join(directory, '.torque', 'actions'), { recursive: true });
  const prefix = `Organization ${Date.now()}`; const group = `${prefix} A`, other = `${prefix} B`;
  let socket: WebSocketRoute | undefined; let connections = 0;
  const tasks: string[] = []; const writes: Row[] = []; const names: Record<string, string> = {}; const evidence: Row[] = [];
  const snapshot = () => command(request, { cmd: 'get_state' });
  const agent = async (id: string) => ((await snapshot()).agents as Record<string, Row>)[id]!;
  const groupOrder = async (name: string) => ((await snapshot()).groups as Record<string, string[]>)[name]!;
  const children = async (id: string) => ((await snapshot()).children as Record<string, string[]>)[id] ?? [];
  const row = (id: string) => page.locator(`[role="treeitem"][data-agent-id="${id}"]`);
  const displayed = async (ids: string[]) => (await page.getByRole('treeitem').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-agent-id')))).filter((id) => ids.includes(id ?? ''));
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (['move_agent', 'reparent_terminal', 'reorder_child'].includes(String(data.cmd))) writes.push(data); server.send(raw); });
    server.onMessage((raw) => client.send(raw));
  });
  try {
    for (const name of [group, other]) {
      await command(request, { cmd: 'add_group', group: name });
      await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: directory, default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false, agent_idle_timeout: 0 } });
    }
    await command(request, { cmd: 'save_action', group, scope: 'project', name: 'qa/organization', action: { prompt: 'Local organization fixture: {{ TASK }}' } });
    const add = async (kind: string, name: string, target = group, extra: Row = {}) => {
      const id = String((await command(request, { cmd: `add_${kind}`, group: target, name, provider: 'generic', command: '/bin/cat', directory, shell: '/bin/sh', ...extra })).id);
      expect(id).not.toBe('undefined'); names[id] = name; return id;
    };
    const alpha = await add('worker', 'Root Alpha'); const beta = await add('worker', 'Root Beta'); const gamma = await add('worker', 'Root Gamma');
    const engineer = await add('engineer', 'Owner Engineer'); const remote = await add('worker', 'Other group Worker', other);
    const owned = async (name: string) => {
      const tid = String((await command(request, { cmd: 'board_add_task', group, task: name, lane: 'Backlog', action_name: 'qa/organization' })).task_id); tasks.push(tid);
      await command(request, { cmd: 'dispatch_task', id: tid, create_agent: true, name, owner_engineer_id: engineer });
      const state = await snapshot(); const id = String((state.board_tasks as Record<string, Row>)[tid]!.agent_id);
      expect((state.agents as Record<string, Row>)[id]).toMatchObject({ owner_engineer_id: engineer, group }); names[id] = name; return id;
    };
    const ownedFirst = await owned('Owned First'); const ownedSecond = await owned('Owned Second');
    const loose = await add('terminal', 'Loose shell');
    const childFirst = await add('terminal', 'Child First', group, { parent_id: engineer });
    const childSecond = await add('terminal', 'Child Second', group, { parent_id: engineer });
    const otherChild = await add('terminal', 'Other child', other, { parent_id: remote });
    const original = (await snapshot()).agents as Record<string, Row>;
    await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await expect(row(alpha)).toBeVisible();
    const apply = async (id: string, values: { parent?: string; group?: string; before?: string }, cancel = false) => {
      const dialog = await organize(page, names[id]!);
      if (values.parent !== undefined) await dialog.getByRole('combobox', { name: 'Terminal parent', exact: true }).selectOption(values.parent);
      if (values.group !== undefined) await dialog.getByRole('combobox', { name: 'Group', exact: true }).selectOption(values.group);
      if (values.before !== undefined) await dialog.getByRole('combobox', { name: 'Position', exact: true }).selectOption(values.before);
      if (cancel) {
        const previous = connections; await socket!.close({ code: 1012, reason: 'Organization draft reconnect' });
        await expect.poll(() => connections).toBeGreaterThan(previous);
        await expect(dialog.getByRole('combobox', { name: 'Terminal parent', exact: true })).toHaveValue(values.parent!);
      }
      await dialog.getByRole('button', { name: cancel ? 'Cancel' : 'Apply', exact: true }).click(); await expect(dialog).toHaveCount(0);
    };
    await apply(gamma, { before: alpha });
    await expect.poll(async () => (await groupOrder(group)).filter((id) => [alpha, beta, gamma].includes(id))).toEqual([gamma, alpha, beta]);
    await expect.poll(() => displayed([alpha, beta, gamma])).toEqual([gamma, alpha, beta]);
    await apply(gamma, { before: '' }); await expect.poll(() => displayed([alpha, beta, gamma])).toEqual([alpha, beta, gamma]);
    await apply(ownedSecond, { before: ownedFirst });
    await expect.poll(() => displayed([ownedFirst, ownedSecond])).toEqual([ownedSecond, ownedFirst]);
    await expect(row(ownedFirst)).toHaveAttribute('aria-level', '2'); await expect(row(ownedSecond)).toHaveAttribute('aria-level', '2');
    await apply(loose, { parent: engineer, before: childFirst });
    await expect.poll(() => children(engineer)).toEqual([loose, childFirst, childSecond]); await expect(row(loose)).toHaveAttribute('aria-level', '2');
    await apply(childSecond, { before: loose });
    await expect.poll(() => children(engineer)).toEqual([childSecond, loose, childFirst]);
    await expect.poll(() => displayed([loose, childFirst, childSecond])).toEqual([childSecond, loose, childFirst]);
    const cancelledCount = writes.length; await apply(loose, { parent: remote }, true); expect(writes).toHaveLength(cancelledCount); expect((await agent(loose)).parent_id).toBe(engineer);
    await apply(loose, { parent: remote, before: otherChild });
    await expect.poll(() => children(remote)).toEqual([loose, otherChild]); await expect.poll(() => children(engineer)).toEqual([childSecond, childFirst]);
    expect(await agent(loose)).toMatchObject({ parent_id: remote, group: other, session_id: original[loose]!.session_id });
    await page.getByRole('button', { name: other, exact: true }).click(); await expect(row(loose)).toHaveAttribute('aria-level', '2');
    await apply(otherChild, { parent: '', group, before: alpha });
    await expect.poll(async () => (await agent(otherChild)).parent_id).toBe('');
    expect(await agent(otherChild)).toMatchObject({ group, session_id: original[otherChild]!.session_id }); await expect.poll(() => children(remote)).toEqual([loose]);
    await page.getByRole('button', { name: group, exact: true }).click(); await expect(row(otherChild)).toHaveAttribute('aria-level', '1');
    await apply(engineer, { group: other, before: remote });
    await expect.poll(async () => (await agent(childFirst)).group).toBe(other); expect((await agent(childSecond)).group).toBe(other);
    for (const id of [ownedFirst, ownedSecond]) { expect(await agent(id)).toMatchObject({ group, owner_engineer_id: engineer }); await expect(row(id)).toHaveAttribute('aria-level', '1'); }
    evidence.push({ phase: 'owner-moved', owner: await agent(engineer), owned: [await agent(ownedFirst), await agent(ownedSecond)], children: await children(engineer) });
    await page.getByRole('button', { name: other, exact: true }).click(); await expect(row(childFirst)).toHaveAttribute('aria-level', '2');
    await apply(engineer, { group, before: alpha }); await page.getByRole('button', { name: group, exact: true }).click();
    await expect(row(ownedFirst)).toHaveAttribute('aria-level', '2'); await expect(row(ownedSecond)).toHaveAttribute('aria-level', '2');
    await apply(beta, { group: other, before: remote });
    await expect.poll(async () => (await groupOrder(other)).filter((id) => [beta, remote].includes(id))).toEqual([beta, remote]);
    await page.reload(); await expect(row(otherChild)).toHaveAttribute('aria-level', '1');
    await expect.poll(() => displayed([childFirst, childSecond])).toEqual([childSecond, childFirst]);
    await expect.poll(() => displayed([ownedFirst, ownedSecond])).toEqual([ownedSecond, ownedFirst]);
    const final = (await snapshot()).agents as Record<string, Row>;
    for (const id of Object.keys(names)) expect(final[id]!.session_id).toBe(original[id]!.session_id);
    expect(final[ownedFirst]!.owner_engineer_id).toBe(engineer); expect(final[ownedSecond]!.owner_engineer_id).toBe(engineer);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('organization-restored.png') });
    const readOffline = async () => {
      const script = `import importlib.util, json, sys
from importlib.machinery import SourceFileLoader
loader = SourceFileLoader('organization_cli', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)
cli.TORQUE_DB = sys.argv[2]
print(json.dumps(cli.db_read_state()))`;
      const result = await promisify(execFile)(process.env.TORQUE_PTY_PYTHON || 'python3', ['-c', script, fileURLToPath(new URL('../../bin/torque', import.meta.url)), join(String(runtime.data_dir), 'torque.db')]);
      return JSON.parse(result.stdout) as Row;
    };
    await expect.poll(async () => ((await readOffline()).children as Row)[engineer]).toEqual([childSecond, childFirst]);
    const offline = await readOffline();
    expect((offline.children as Row)[remote]).toEqual([loose]);
    expect((offline.groups as Row)[group]).toEqual(await groupOrder(group));
    expect((offline.agents as Record<string, Row>)[ownedFirst]!.owner_engineer_id).toBe(engineer);
    evidence.push({ phase: 'offline-sqlite', children: offline.children, groups: offline.groups });
    evidence.push({ phase: 'final', groups: { [group]: await groupOrder(group), [other]: await groupOrder(other) }, children: { [engineer]: await children(engineer), [remote]: await children(remote) }, writes });
    const path = test.info().outputPath('organization-evidence.json'); await writeFile(path, JSON.stringify(evidence, null, 2)); await test.info().attach('organization-evidence', { path, contentType: 'application/json' });
  } finally {
    for (const name of [group, other]) await command(request, { cmd: 'remove_group', group: name });
    for (const id of tasks) await command(request, { cmd: 'board_remove_task', id });
    await rm(directory, { recursive: true, force: true });
  }
});
