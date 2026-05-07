/* First-run welcome/onboarding flow. */
var _welcomeOpenedAutomatically = false;

function _ensureWelcomeModal() {
  var existing = document.getElementById('modal-welcome');
  if (existing) return existing;
  var overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.id = 'modal-welcome';
  overlay.innerHTML = ''
    + '<div class="modal modal-wide welcome-modal">'
    + '  <div class="welcome-eyebrow">Welcome to Torque</div>'
    + '  <h2>Run agent teams from one local workspace</h2>'
    + '  <p class="welcome-lede">Torque coordinates architects, engineers, workers, terminals, and board tasks while SQLite keeps the source of truth local.</p>'
    + '  <ol class="welcome-steps">'
    + '    <li><strong>Groups</strong><span>collect agents and tasks for one project or stream.</span></li>'
    + '    <li><strong>Architects</strong><span>scope and dispatch work.</span></li>'
    + '    <li><strong>Engineers</strong><span>coordinate workers and review implementation flow.</span></li>'
    + '    <li><strong>Workers</strong><span>execute focused tasks in safe worktrees.</span></li>'
    + '    <li><strong>Board tasks</strong><span>track what is queued, running, reviewing, and done.</span></li>'
    + '  </ol>'
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
