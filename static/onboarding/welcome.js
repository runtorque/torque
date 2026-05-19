/* First-run welcome/onboarding flow. */
var _welcomeOpenedAutomatically = false;

function _welcomeSurfaceCards() {
  var cards = [
    [
      'Groups & views',
      'Start in a group tab, then switch Grid/Canvas: Grid for live terminals, Canvas for the agent tree.'
    ],
    [
      'Agent workflow',
      'Architects scope work, Engineers coordinate, and Workers execute focused tasks in isolated worktrees.'
    ],
    [
      'Board & actions',
      'Use Board lanes to plan, dispatch, review, and finish tasks; use Actions for reusable prompts and pipelines.'
    ],
    [
      'Shared Context',
      'Publish durable findings, decisions, warnings, and handoffs so future agents do not rediscover them.'
    ],
    [
      'Routing library',
      'Roles shape Worker prompts; Specializations advertise Engineer focus so tasks land with the right teammate.'
    ],
  ];
  var html = '<ol class="welcome-steps">';
  for (var i = 0; i < cards.length; i++) {
    html += '<li><strong>' + cards[i][0] + '</strong><span>' + cards[i][1] + '</span></li>';
  }
  html += '</ol>';
  return html;
}

function _ensureWelcomeModal() {
  var existing = document.getElementById('modal-welcome');
  if (existing) return existing;
  var overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.id = 'modal-welcome';
  overlay.innerHTML = ''
    + '<div class="modal modal-wide welcome-modal" role="dialog" aria-modal="true" aria-labelledby="welcome-title">'
    + '  <div class="welcome-eyebrow">Welcome to Torque</div>'
    + '  <h2 id="welcome-title">Orient your local agent workspace</h2>'
    + '  <p class="welcome-lede">Torque keeps agent teams, tasks, prompts, and shared context together while SQLite stays the local source of truth.</p>'
    + _welcomeSurfaceCards()
    + '  <div class="welcome-actions">'
    + '    <button class="btn-secondary" type="button" onclick="welcomeCreateSampleGroup()">Create sample group</button>'
    + '    <button class="btn-cancel" type="button" onclick="closeWelcome({ complete: false })">Show later</button>'
    + '    <button class="btn-primary" type="button" onclick="closeWelcome({ complete: true })">Get Started</button>'
    + '  </div>'
    + '</div>';
  document.body.appendChild(overlay);
  return overlay;
}

function openWelcome(opts) {
  opts = opts || {};
  var modal = _ensureWelcomeModal();
  modal.classList.add('visible');
  if (opts.automatic) _welcomeOpenedAutomatically = true;
}

function _markWelcomeComplete() {
  try { localStorage.setItem('torque.first_run_complete', '1'); } catch (_) {}
  if (typeof send === 'function') send({ cmd: 'first_run_complete' });
  else {
    fetch('/api/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: 'first_run_complete' }),
    }).catch(function() {});
  }
}

function closeWelcome(opts) {
  opts = opts || {};
  var modal = document.getElementById('modal-welcome');
  if (modal) modal.classList.remove('visible');
  if (opts.complete) _markWelcomeComplete();
}

function welcomeCreateSampleGroup() {
  if (typeof openAddGroup === 'function') openAddGroup();
  if (typeof _showToast === 'function') _showToast('Name the group “Sample” to start a demo workspace.', 'info');
}

function _shouldShowWelcomeFromUrl() {
  if (typeof URLSearchParams === 'undefined' || typeof location === 'undefined') return false;
  return new URLSearchParams(location.search || '').has('onboarding');
}

function maybeOpenWelcomeOnBoot() {
  if (_welcomeOpenedAutomatically) return;
  if (_shouldShowWelcomeFromUrl()) {
    openWelcome({ automatic: true });
    return;
  }
  var mode = (typeof _torqueUiMode === 'function') ? _torqueUiMode() : '';
  if (mode === 'standalone') {
    var complete = false;
    try { complete = localStorage.getItem('torque.first_run_complete') === '1'; } catch (_) {}
    if (!complete) openWelcome({ automatic: true });
  }
}
