import { readFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const indexPath = resolve(root, 'dist/index.html');
const manifestPath = resolve(root, 'dist/.vite/manifest.json');

await Promise.all([stat(indexPath), stat(manifestPath)]);
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const entry = manifest['index.html'];

if (!entry?.isEntry || typeof entry.file !== 'string') {
  throw new Error('Vite manifest is missing the index.html entry asset');
}

await stat(resolve(root, 'dist', entry.file));
