'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function webviewScriptSources(html) {
  const source = html == null
    ? fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8')
    : String(html);
  return Array.from(
    source.matchAll(/<script\s+[^>]*src=["']\/([^"']+)["'][^>]*><\/script>/g),
    (match) => match[1],
  );
}

function loadFrontendScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function loadWebviewScriptsThrough(context, finalScript, options = {}) {
  const sources = webviewScriptSources(options.html);
  const finalIndex = sources.indexOf(finalScript);
  if (finalIndex < 0) {
    throw new Error(`Frontend script is not present in webview.html: ${finalScript}`);
  }
  const skip = new Set(options.skip || []);
  for (const source of sources.slice(0, finalIndex + 1)) {
    if (!skip.has(source)) loadFrontendScript(context, source);
  }
  return sources.slice(0, finalIndex + 1);
}

module.exports = {
  repoRoot,
  webviewScriptSources,
  loadFrontendScript,
  loadWebviewScriptsThrough,
};
