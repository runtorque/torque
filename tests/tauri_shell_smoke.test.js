const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const repoRoot = path.resolve(__dirname, '..');

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

function postJson(port, body, timeoutMs = 500) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = http.request({
      hostname: '127.0.0.1',
      port,
      path: '/api/cmd',
      method: 'POST',
      timeout: timeoutMs,
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
    }, (res) => {
      let raw = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(raw));
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('request timed out')));
    req.on('error', reject);
    req.end(payload);
  });
}

async function waitForRuntime(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const payload = await postJson(port, { cmd: 'get_config' });
      if (payload.ok && payload.data && payload.data.runtime) {
        return payload.data.runtime;
      }
    } catch (err) {
      lastError = err;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw lastError || new Error(`Timed out waiting for runtime on ${port}`);
}

function getHtml(port) {
  return new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}/`, (res) => {
      let raw = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => resolve(raw));
    }).on('error', reject);
  });
}

function openWs(port) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
    const timeout = setTimeout(() => {
      ws.close();
      reject(new Error('Timed out waiting for /ws snapshot'));
    }, 5000);
    ws.addEventListener('message', (event) => {
      clearTimeout(timeout);
      ws.close();
      resolve(String(event.data));
    }, { once: true });
    ws.addEventListener('error', (event) => {
      clearTimeout(timeout);
      reject(event.error || new Error('WebSocket error'));
    }, { once: true });
  });
}

function sanitizedTorqueEnv(extra) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith('TORQUE_')) {
      delete env[key];
    }
  }
  return { ...env, ...extra };
}

test('Tauri shell smoke against an isolated daemon', { timeout: 45000, skip: !process.env.RUN_TAURI_SMOKE && 'set RUN_TAURI_SMOKE=1 to run native Tauri smoke' }, async (t) => {
  const cargoTauri = spawnSync('cargo', ['tauri', '--version'], {
    cwd: path.join(repoRoot, 'src-tauri'),
    encoding: 'utf8',
  });
  assert.equal(cargoTauri.status, 0, cargoTauri.stderr || cargoTauri.stdout || 'cargo tauri is unavailable');

  const port = await getFreePort();
  const profile = `tauri-smoke-${process.pid}-${Date.now()}`;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `${profile}-`));
  const python = process.env.TORQUE_PYTHON_EXECUTABLE || process.env.PYTHON || 'python3';

  const daemon = spawn(python, [path.join(repoRoot, 'torque.py')], {
    cwd: repoRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: sanitizedTorqueEnv({
      TORQUE_STANDALONE: '1',
      TORQUE_PORT: String(port),
      TORQUE_PROFILE: profile,
      TORQUE_DATA_DIR: dataDir,
    }),
  });
  t.after(() => {
    daemon.kill('SIGTERM');
    fs.rmSync(dataDir, { recursive: true, force: true });
  });

  const runtime = await waitForRuntime(port);
  assert.equal(runtime.standalone, true);
  assert.equal(runtime.profile, profile);

  const html = await getHtml(port);
  assert.match(html, /xterm/i, 'daemon-served shell should include xterm assets');

  const wsMessage = await openWs(port);
  assert.match(wsMessage, /snapshot|groups|cells|tasks/, 'main WebSocket should emit state');

  const tauri = spawn('cargo', ['tauri', 'dev'], {
    cwd: path.join(repoRoot, 'src-tauri'),
    stdio: ['ignore', 'pipe', 'pipe'],
    env: sanitizedTorqueEnv({
      TORQUE_REPO_ROOT: repoRoot,
      TORQUE_PYTHON_EXECUTABLE: python,
      TORQUE_DESKTOP_MODE: 'attach',
      TORQUE_DESKTOP_PORT: String(port),
      TORQUE_DESKTOP_PROFILE: profile,
      TORQUE_DESKTOP_DATA_DIR: dataDir,
      TORQUE_PORT: String(port),
      TORQUE_PROFILE: profile,
      TORQUE_DATA_DIR: dataDir,
    }),
  });
  let output = '';
  tauri.stdout.on('data', (chunk) => { output += chunk.toString(); });
  tauri.stderr.on('data', (chunk) => { output += chunk.toString(); });
  t.after(() => tauri.kill('SIGTERM'));

  await new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, 7000);
    tauri.once('exit', (code, signal) => {
      clearTimeout(timer);
      reject(new Error(`cargo tauri dev exited early code=${code} signal=${signal}\n${output}`));
    });
  });
  assert.doesNotMatch(output, /console error|uncaught|panic/i);
});
