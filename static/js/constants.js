/* System label helpers */
function isSystemLabel(l) { return l.startsWith('loom:'); }
function displayLabel(l) { return isSystemLabel(l) ? l.slice(5) : l; }

/* When true, only show agents/terminals belonging to the current window.
   Reads from global settings state; falls back to true before state loads. */
function getFilterByWindow() {
  return (state && state.global_settings &&
          state.global_settings.filter_by_window !== undefined)
    ? state.global_settings.filter_by_window : true;
}

const AGENT_ICONS = ['\u2B21','\u25C8','\u25C6','\u25A3','\u2B22','\u25C9','\u25CE','\u25B2','\u2B1F','\u23E3'];

const PROCESS_MAP = {
  'zsh':     { label: '\u276F_',  color: '#3fb950' },
  'bash':    { label: '\u276F_',  color: '#3fb950' },
  'fish':    { label: '\u276F_',  color: '#d2a04e' },
  'sh':      { label: '\u276F_',  color: '#6e7681' },
  'login':   { label: '\u276F_',  color: '#3fb950' },
  'vim':     { label: 'VIM', color: '#019833' },
  'nvim':    { label: 'VIM', color: '#57a143' },
  'vi':      { label: 'VI',  color: '#019833' },
  'nano':    { label: 'NAN', color: '#4a90d9' },
  'emacs':   { label: 'EMX', color: '#7f5ab6' },
  'hx':      { label: 'HLX', color: '#583674' },
  'code':    { label: 'VSC', color: '#007acc' },
  'python':  { label: 'PY',  color: '#3572a5' },
  'python3': { label: 'PY',  color: '#3572a5' },
  'ipython': { label: 'PY',  color: '#3572a5' },
  'node':    { label: 'JS',  color: '#8cc84b' },
  'deno':    { label: 'DNO', color: '#01c2ff' },
  'bun':     { label: 'BUN', color: '#fbf0df', dark: true },
  'ruby':    { label: 'RB',  color: '#cc342d' },
  'irb':     { label: 'RB',  color: '#cc342d' },
  'go':      { label: 'GO',  color: '#00add8' },
  'cargo':   { label: 'RS',  color: '#dea584', dark: true },
  'rustc':   { label: 'RS',  color: '#dea584', dark: true },
  'java':    { label: 'JV',  color: '#b07219' },
  'swift':   { label: 'SW',  color: '#f05138' },
  'lua':     { label: 'LUA', color: '#2c2d72' },
  'perl':    { label: 'PL',  color: '#0298c3' },
  'php':     { label: 'PHP', color: '#4f5d95' },
  'ssh':     { label: 'SSH', color: '#58a6ff' },
  'mosh':    { label: 'SSH', color: '#58a6ff' },
  'docker':  { label: 'DKR', color: '#2496ed' },
  'kubectl': { label: 'K8S', color: '#326ce5' },
  'git':     { label: 'GIT', color: '#f05032' },
  'make':    { label: 'MK',  color: '#d29922' },
  'npm':     { label: 'NPM', color: '#cb3837' },
  'yarn':    { label: 'YRN', color: '#2c8ebb' },
  'pip':     { label: 'PIP', color: '#3572a5' },
  'claude':  { label: 'CL',  color: '#d4a574' },
  'aider':   { label: 'AID', color: '#14b8a6' },
  'top':     { label: 'TOP', color: '#6e7681' },
  'htop':    { label: 'HTP', color: '#6e7681' },
  'btop':    { label: 'BTP', color: '#6e7681' },
  'less':    { label: '\u2026',  color: '#6e7681' },
  'man':     { label: 'MAN', color: '#6e7681' },
  'curl':    { label: 'CRL', color: '#073551' },
  'wget':    { label: 'WGT', color: '#073551' },
};

/* Agent type display labels */
const AGENT_TYPE_LABELS = {
  'claude-code': { label: 'Claude', short: 'CC' },
  'codex':       { label: 'Codex',  short: 'CX' },
  'gemini-cli':  { label: 'Gemini', short: 'GM' },
  'generic':     { label: '',       short: '' },
};


/* Deterministic label color from string hash.
   Returns an HSL color string suitable for dark backgrounds. */
const _LABEL_PALETTE = [
  '#f85149', '#d29922', '#3fb950', '#2ea8a1', '#58a6ff',
  '#a371f7', '#f778ba', '#d4a574', '#7ee787', '#79c0ff',
  '#d2a8ff', '#ff9bce', '#ffa657', '#56d4dd', '#b3d364',
];
function labelColor(name) {
  var h = 0;
  for (var i = 0; i < name.length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  }
  return _LABEL_PALETTE[((h < 0 ? -h : h) % _LABEL_PALETTE.length)];
}

const TAB_COLORS = [
  { name: 'Red',    hex: '#f85149' },
  { name: 'Orange', hex: '#d29922' },
  { name: 'Yellow', hex: '#e3b341' },
  { name: 'Green',  hex: '#3fb950' },
  { name: 'Teal',   hex: '#2ea8a1' },
  { name: 'Blue',   hex: '#58a6ff' },
  { name: 'Purple', hex: '#a371f7' },
  { name: 'Pink',   hex: '#f778ba' },
];
