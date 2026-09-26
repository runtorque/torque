import { execFileSync, spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { expect, test as base } from '@playwright/test';

type Runtime = { profile: string; port: number; pid: number; data_dir: string; supervisor?: { session_count?: number } };
async function freePort() {
  const server = createServer();
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Could not reserve an isolated QA port');
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}
function processCommand(pid: number) {
  try { return execFileSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8' }).trim(); }
  catch (cause) { if ((cause as { status?: number }).status === 1) return ''; throw cause; }
}

async function cleanupHelpers(directory: string) {
  // Helpers intentionally outlive normal daemon shutdown. Stop only the
  // helper whose live command references this freshly created data directory.
  for (const name of ['pty_supervisor.pid', 'event_ingest.pid']) {
    let pid: number;
    try { pid = Number((await readFile(join(directory, name), 'utf8')).trim()); }
    catch (cause) { if ((cause as NodeJS.ErrnoException).code === 'ENOENT') continue; throw cause; }
    if (!Number.isInteger(pid) || pid <= 1) throw new Error(`Invalid QA helper PID in ${name}`);
    const command = processCommand(pid); if (!command) continue;
    if (!command.includes(directory)) throw new Error(`QA helper identity mismatch for ${name}; retained ${directory}`);
    process.kill(pid, 'SIGTERM'); await expect.poll(() => processCommand(pid)).toBe('');
  }
}

// This test needs real environment/file precedence. Own that configuration rather
// than requiring a historically named daemon or changing the shared suite profile.
export const test = base.extend<{ relayProfile: string }>({
  relayProfile: [async ({ browserName }, provide, info) => {
    const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-relay-config-')));
    const port = await freePort(); const profile = `qa-relay-config-${browserName}-${port}`;
    const logPath = join(directory, 'launcher.log'); const output = createWriteStream(logPath);
    await writeFile(join(directory, 'ee_connector.json'), JSON.stringify({ relay_url: 'wss://relay-file.invalid/ws', daemon_id: 'qa-file-daemon', private_key_pem: 'QA_INLINE_SENTINEL_NOT_A_KEY' }));
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('TORQUE_')));
    const child = spawn(process.env.TORQUE_QA_PYTHON || process.env.TORQUE_PTY_PYTHON || 'python3', ['torque.py'], {
      cwd: fileURLToPath(new URL('../..', import.meta.url)),
      env: { ...env, TORQUE_STANDALONE: '1', TORQUE_DATA_DIR: directory, TORQUE_PROFILE: profile, TORQUE_PORT: String(port), TORQUE_CLOUD_CONNECTOR_ENABLED: '1', TORQUE_CLOUD_RELAY_URL: 'wss://relay-env.invalid/ws', TORQUE_EE_DAEMON_CREDENTIAL_ID: 'qa-env-credential', TORQUE_CLOUD_CONNECTOR_MODULE: 'torque_qa_intentionally_unavailable_connector' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.pipe(output, { end: false }); child.stderr.pipe(output, { end: false });
    let exited = false; let spawnError: Error | undefined;
    child.once('error', (error) => { spawnError = error; }); child.once('exit', () => { exited = true; });
    const url = `http://127.0.0.1:${port}`;
    try {
      await expect.poll(async () => {
        if (spawnError) throw spawnError;
        if (exited) throw new Error(`Relay QA daemon exited: ${await readFile(logPath, 'utf8')}`);
        try { const response = await fetch(`${url}/api/runtime`, { signal: AbortSignal.timeout(1000) }); const body = await response.json() as { data: { runtime: Runtime } }; return body.data.runtime; }
        catch { return null; }
      }, { timeout: 30_000 }).toMatchObject({ profile, port, pid: child.pid, data_dir: directory, supervisor: { session_count: 0 } });
      await provide(url);
    } finally {
      if (!exited) child.kill('SIGTERM');
      for (let attempt = 0; !exited && attempt < 50; attempt++) await delay(100);
      if (!exited) { child.kill('SIGKILL'); await expect.poll(() => exited).toBe(true); }
      await cleanupHelpers(directory);
      await new Promise<void>((resolve) => output.end(resolve));
      await info.attach('Relay configuration daemon log', { path: logPath, contentType: 'text/plain' });
      await rm(directory, { recursive: true, force: true });
    }
  }, { timeout: 60_000 }],
  baseURL: async ({ relayProfile }, provide) => { await provide(relayProfile); },
});
