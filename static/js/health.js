/* System health metrics dock panel */

var healthState = {
  window: '24h',
  scope: 'active',
  loading: false,
  error: '',
  payload: null,
  requestedKey: '',
  loadedKey: '',
  lastRequestedAt: 0,
  refreshTimer: 0,
};

function _healthEsc(value) {
  if (typeof esc === 'function') return esc(value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _healthActiveGroup() {
  if (typeof _activeGroup === 'function') return String(_activeGroup() || '');
  if (state && state.active_group) return String(state.active_group || '');
  var groups = Object.keys((state && state.groups) || {});
  return groups.length ? groups[0] : '';
}

function _healthRequestGroup() {
  return healthState.scope === 'all' ? '' : _healthActiveGroup();
}

function _healthCacheKey() {
  return healthState.window + '::' + _healthRequestGroup();
}

function _healthVisible() {
  if (typeof _panelAppVisible === 'function') return _panelAppVisible('health');
  return typeof _activePanelApp !== 'undefined' && _activePanelApp === 'health';
}

function _healthFormatNumber(value, digits) {
  var n = Number(value || 0);
  if (!Number.isFinite(n)) n = 0;
  if (Math.abs(n) >= 100) return String(Math.round(n));
  return n.toFixed(digits == null ? 1 : digits).replace(/\.0$/, '');
}

function _healthFormatDuration(seconds) {
  var n = Number(seconds || 0);
  if (!Number.isFinite(n) || n <= 0) return '0m';
  if (n < 3600) return Math.round(n / 60) + 'm';
  if (n < 86400) return _healthFormatNumber(n / 3600, 1) + 'h';
  return _healthFormatNumber(n / 86400, 1) + 'd';
}

function _healthPercent(value) {
  var n = Number(value || 0);
  if (!Number.isFinite(n)) n = 0;
  return _healthFormatNumber(n * 100, 1) + '%';
}

function _healthRawPercent(value) {
  var n = Number(value || 0);
  if (!Number.isFinite(n)) n = 0;
  return _healthFormatNumber(n, 1) + '%';
}

function _healthSeriesPoints(values, width, height) {
  var arr = Array.isArray(values) ? values : [];
  if (!arr.length) return '';
  var max = arr.reduce(function(acc, item) {
    var n = Number(item || 0);
    return Math.max(acc, Number.isFinite(n) ? n : 0);
  }, 0);
  if (max <= 0) max = 1;
  var step = arr.length > 1 ? width / (arr.length - 1) : width;
  return arr.map(function(item, idx) {
    var n = Number(item || 0);
    if (!Number.isFinite(n)) n = 0;
    var x = Math.round(idx * step * 100) / 100;
    var y = Math.round((height - ((n / max) * (height - 4)) - 2) * 100) / 100;
    return x + ',' + y;
  }).join(' ');
}

function _healthSparkline(values, label) {
  var points = _healthSeriesPoints(values, 140, 34);
  return '<svg class="health-sparkline" viewBox="0 0 140 34" role="img" aria-label="'
    + _healthEsc(label || 'trend') + '">'
    + '<polyline points="' + _healthEsc(points) + '" fill="none" stroke="currentColor" stroke-width="2"/>'
    + '</svg>';
}

function _healthSummaryCard(title, value, detail, series, className) {
  return '<article class="health-card ' + _healthEsc(className || '') + '">'
    + '<div class="health-card-title">' + _healthEsc(title) + '</div>'
    + '<div class="health-card-value">' + _healthEsc(value) + '</div>'
    + '<div class="health-card-detail">' + _healthEsc(detail || '') + '</div>'
    + (series ? _healthSparkline(series, title) : '')
    + '</article>';
}

function _healthAgeTable(payload) {
  var lanes = (payload && payload.distributions && payload.distributions.task_age_by_lane) || {};
  var names = Object.keys(lanes).sort();
  if (!names.length) {
    return '<div class="health-empty">No current task age data for this scope.</div>';
  }
  var rows = names.map(function(lane) {
    var item = lanes[lane] || {};
    var buckets = item.buckets || {};
    var bucketText = Object.keys(buckets).map(function(name) {
      return name + ':' + buckets[name];
    }).join(' · ');
    return '<tr>'
      + '<th scope="row">' + _healthEsc(lane) + '</th>'
      + '<td>' + _healthEsc(item.count || 0) + '</td>'
      + '<td>' + _healthEsc(_healthFormatDuration(item.p50_seconds)) + '</td>'
      + '<td>' + _healthEsc(_healthFormatDuration(item.p90_seconds)) + '</td>'
      + '<td>' + _healthEsc(_healthFormatDuration(item.max_seconds)) + '</td>'
      + '<td class="health-age-buckets">' + _healthEsc(bucketText) + '</td>'
      + '</tr>';
  }).join('');
  return '<table class="health-age-table">'
    + '<thead><tr><th>Lane</th><th>Tasks</th><th>p50</th><th>p90</th><th>Max</th><th>Buckets</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table>';
}

function _healthCoverageHtml(payload) {
  var coverage = (payload && payload.coverage && payload.coverage.dispatch_shape) || {};
  var notes = Array.isArray(payload && payload.notes) ? payload.notes : [];
  var partial = coverage.partial ? 'Partial' : 'Complete';
  var html = '<div class="health-coverage">'
    + '<span class="health-pill ' + (coverage.partial ? 'health-pill-warn' : '') + '">'
    + _healthEsc(partial) + ' dispatch-shape coverage</span>'
    + '<span>' + _healthEsc(coverage.dispatch_tool_entries || 0) + ' tool entries / '
    + _healthEsc(coverage.dispatch_events || 0) + ' dispatch events</span>'
    + '</div>';
  if (notes.length) {
    html += '<ul class="health-notes">' + notes.map(function(note) {
      return '<li>' + _healthEsc(note) + '</li>';
    }).join('') + '</ul>';
  }
  return html;
}

function _healthResultsHtml(payload) {
  if (healthState.error) {
    return '<div class="health-error">' + _healthEsc(healthState.error) + '</div>';
  }
  if (healthState.loading && !payload) {
    return '<div class="health-loading">Loading health metrics…</div>';
  }
  if (!payload) {
    return '<div class="health-empty">Open the panel to load health metrics.</div>';
  }
  var summary = payload.summary || {};
  var series = payload.series || {};
  var dispatch = summary.dispatch || {};
  var shape = summary.dispatch_shape || {};
  var review = summary.review_cycles || {};
  var merge = summary.merge || {};
  var doa = summary.worker_boot_doa || {};
  var util = summary.utilization || {};
  var cards = [
    _healthSummaryCard(
      'Dispatch throughput',
      String(dispatch.count || 0),
      _healthFormatNumber(dispatch.workers_per_hour || 0, 2) + ' workers/hour',
      series.dispatches,
      'health-card-dispatch'
    ),
    _healthSummaryCard(
      'Dispatch shape',
      (shape.batch_tool_calls || 0) + ' batch / ' + (shape.serial_tool_calls || 0) + ' serial',
      (shape.batch_entries || 0) + ' batch entries recorded',
      null,
      'health-card-shape'
    ),
    _healthSummaryCard(
      'Review cycles',
      _healthFormatNumber(review.average_rounds || 0, 2) + ' avg',
      _healthPercent(review.first_pass_clean_pct || 0) + ' first-pass clean',
      series.reviews,
      'health-card-review'
    ),
    _healthSummaryCard(
      'Merge throughput',
      String(merge.merged_count || 0),
      _healthFormatDuration(merge.median_boundary_to_merge_seconds || 0) + ' median boundary→merge',
      series.merges,
      'health-card-merge'
    ),
    _healthSummaryCard(
      'Worker boot DOA',
      _healthPercent(doa.rate || 0),
      (doa.count || 0) + ' / ' + (doa.denominator || 0) + ' dispatches',
      series.worker_boot_doa,
      'health-card-doa'
    ),
    _healthSummaryCard(
      'Engineer plate utilization',
      _healthRawPercent(util.percent || 0),
      _healthFormatDuration(util.busy_seconds || 0) + ' busy worker time',
      series.utilization_pct,
      'health-card-util'
    ),
  ].join('');
  var bucketLabel = (payload.bucket_seconds || 0) < 86400 ? 'hourly' : 'daily';
  return '<div class="health-meta">'
    + '<span>' + _healthEsc(payload.window || '') + ' · ' + _healthEsc(bucketLabel) + ' buckets</span>'
    + '<span>' + _healthEsc(payload.group || 'All groups') + '</span>'
    + '</div>'
    + '<div class="health-card-grid">' + cards + '</div>'
    + '<section class="health-section"><h3>Task age by lane</h3>'
    + _healthAgeTable(payload) + '</section>'
    + '<section class="health-section"><h3>Coverage and notes</h3>'
    + _healthCoverageHtml(payload) + '</section>';
}

function _healthSyncControls() {
  var win = document.getElementById('health-window-select');
  if (win) win.value = healthState.window;
  var scope = document.getElementById('health-scope-select');
  if (scope) scope.value = healthState.scope;
  var active = document.getElementById('health-active-group-name');
  if (active) active.textContent = _healthActiveGroup() || 'No active group';
}

function renderHealthPanel(opts) {
  opts = opts || {};
  var root = document.getElementById('panel-health');
  if (!root) return;
  if (!root.querySelector || !root.querySelector('.health-panel')) {
    root.innerHTML = '<div class="health-panel">'
      + '<div class="health-toolbar">'
      + '<div><h2>System Health</h2><p>Read-only orchestration metrics over time.</p></div>'
      + '<label>Window <select id="health-window-select" onchange="healthSetWindow(this.value)">'
      + '<option value="24h">24h</option><option value="7d">7d</option><option value="30d">30d</option>'
      + '</select></label>'
      + '<label>Scope <select id="health-scope-select" onchange="healthSetScope(this.value)">'
      + '<option value="active">Active group</option><option value="all">All groups</option>'
      + '</select></label>'
      + '<span class="health-active-group" id="health-active-group-name"></span>'
      + '<button class="btn-secondary" onclick="healthRefresh()">Refresh</button>'
      + '</div>'
      + '<div id="health-results" class="health-results"></div>'
      + '</div>';
  }
  _healthSyncControls();
  _healthUpdateResults();
  if (!opts.skipEnsure) healthEnsureLoaded();
}

function _healthUpdateResults() {
  var root = document.getElementById('panel-health');
  var results = document.getElementById('health-results');
  if (!results) return;
  var scrollTop = root ? root.scrollTop : 0;
  results.innerHTML = _healthResultsHtml(healthState.payload);
  if (root) root.scrollTop = scrollTop;
}

function healthEnsureLoaded(opts) {
  opts = opts || {};
  if (!opts.force && !_healthVisible()) return;
  var key = _healthCacheKey();
  var now = Date.now ? Date.now() : 0;
  if (!opts.force && healthState.loadedKey === key && healthState.payload) {
    if (now - (healthState.lastRequestedAt || 0) < 60000) return;
  }
  if (healthState.loading && healthState.requestedKey === key && !opts.force) return;
  healthState.loading = true;
  healthState.error = '';
  healthState.requestedKey = key;
  healthState.lastRequestedAt = now;
  _healthUpdateResults();
  if (typeof send === 'function') {
    send({
      cmd: 'get_system_health_metrics',
      window: healthState.window,
      group: _healthRequestGroup(),
    });
  }
  _healthScheduleRefresh();
}

function _healthScheduleRefresh() {
  if (healthState.refreshTimer && typeof clearTimeout === 'function') {
    clearTimeout(healthState.refreshTimer);
  }
  if (typeof setTimeout !== 'function') return;
  healthState.refreshTimer = setTimeout(function() {
    healthState.refreshTimer = 0;
    if (_healthVisible()) healthEnsureLoaded({ force: true });
  }, 60000);
}

function healthReceiveMetrics(msg) {
  var incomingKey = '';
  if (msg && msg.type !== 'error') {
    incomingKey = (msg.window ? msg.window : healthState.window)
      + '::' + (msg.group || '');
    if (incomingKey && incomingKey !== _healthCacheKey()
        && incomingKey !== healthState.requestedKey) {
      return;
    }
  }
  healthState.loading = false;
  if (msg && msg.type === 'error') {
    healthState.error = msg.message || 'Unable to load health metrics.';
  } else {
    healthState.error = '';
    healthState.payload = msg || null;
    healthState.loadedKey = incomingKey || ((msg && msg.window ? msg.window : healthState.window)
      + '::' + ((msg && msg.group) || ''));
  }
  _healthSyncControls();
  _healthUpdateResults();
  if (_healthVisible()) _healthScheduleRefresh();
}

function healthSetWindow(value) {
  var next = String(value || '24h');
  if (['24h', '7d', '30d'].indexOf(next) < 0) next = '24h';
  if (healthState.window === next) return;
  healthState.window = next;
  healthState.payload = null;
  renderHealthPanel({ skipEnsure: true });
  healthEnsureLoaded({ force: true });
}

function healthSetScope(value) {
  var next = value === 'all' ? 'all' : 'active';
  if (healthState.scope === next) return;
  healthState.scope = next;
  healthState.payload = null;
  renderHealthPanel({ skipEnsure: true });
  healthEnsureLoaded({ force: true });
}

function healthRefresh() {
  healthEnsureLoaded({ force: true });
}

function healthActiveGroupChanged() {
  _healthSyncControls();
  if (healthState.scope !== 'active') return;
  healthState.payload = null;
  healthState.loadedKey = '';
  if (_healthVisible()) healthEnsureLoaded({ force: true });
  else _healthUpdateResults();
}
