'use strict';

const fs = require('node:fs');
const path = require('node:path');

const {
  repoRoot,
  webviewStylesheetSources,
} = require('./frontend_script_loader');

function appStylesheetSources(html) {
  return webviewStylesheetSources(html).filter((source) => (
    source === 'static/style.css' || source.startsWith('static/styles/')
  ));
}

function appStylesheetSource(html) {
  return appStylesheetSources(html)
    .map((source) => fs.readFileSync(path.join(repoRoot, source), 'utf8'))
    .join('\n');
}

module.exports = {
  appStylesheetSources,
  appStylesheetSource,
};
