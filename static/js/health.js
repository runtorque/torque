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

var HEALTH_METRICS_HISTORY_REFRESH_MS = 60000;
var HEALTH_FRONTEND_RENDER_REPORT_MS = 2000;
var HEALTH_FRONTEND_RENDER_WINDOW_MS = 5000;
var HEALTH_FRONTEND_RENDER_SAMPLE_LIMIT = 240;

var healthMetricsState = {
  historyLoading: false,
  historyError: '',
  historyPayload: null,
  historyRequestedKey: '',
  historyLoadedKey: '',
  historyLastRequestedAt: 0,
  historyRefreshTimer: 0,
  tick: null,
  tickSeenAt: 0,
  expanded: true,
  frontendSamples: [],
  frontendReportTimer: 0,
  frontendEverSampled: false,
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

function _healthMetricsNowMs() {
  if (typeof Date !== 'undefined' && Date && typeof Date.now === 'function') {
    return Date.now();
  }
  return (new Date()).getTime();
}

function _healthMetricFinite(value) {
  var n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function _healthMetricFormat(value, digits, unit) {
  var n = _healthMetricFinite(value);
  if (n === null) return '—';
  return _healthFormatNumber(n, digits == null ? 1 : digits) + (unit || '');
}

function _healthMetricFormatRate(value, digits, suffix) {
  var text = _healthMetricFormat(value, digits == null ? 1 : digits, '');
  return text === '—' ? text : text + (suffix || '/s');
}

function _healthMetricsStatusText() {
  var tick = healthMetricsState.tick;
  if (!tick) return 'Metrics —';
  if (tick.enabled === false) return 'metrics off';
  var perf = (tick && tick.perf) || {};
  var lag = perf.event_loop_lag_ms || {};
  var proc = perf.proc || {};
  return 'lag ' + _healthMetricFormat(lag.p95, 1, 'ms')
    + ' · mem ' + _healthMetricFormat(proc.rss_mb, 0, 'MB');
}

function _healthMetricsTickPerf() {
  var tick = healthMetricsState.tick;
  if (!tick || tick.enabled === false) return null;
  return (tick.perf && typeof tick.perf === 'object') ? tick.perf : null;
}

function _healthMetricsHistoryPerf() {
  var payload = healthMetricsState.historyPayload;
  return (payload && payload.perf && typeof payload.perf === 'object') ? payload.perf : {};
}

function _healthMetricsSeries(keys) {
  var perf = _healthMetricsHistoryPerf();
  keys = Array.isArray(keys) ? keys : [keys];
  for (var i = 0; i < keys.length; i++) {
    var arr = perf[keys[i]];
    if (!Array.isArray(arr)) continue;
    var out = [];
    for (var j = 0; j < arr.length; j++) {
      var n = _healthMetricFinite(arr[j]);
      if (n !== null) out.push(n);
    }
    if (out.length) return out;
  }
  return null;
}

function _healthMetricsLastFinite(values) {
  var arr = Array.isArray(values) ? values : [];
  for (var i = arr.length - 1; i >= 0; i--) {
    var n = _healthMetricFinite(arr[i]);
    if (n !== null) return n;
  }
  return null;
}

function _healthMetricsPreviousFinite(values) {
  var arr = Array.isArray(values) ? values : [];
  var foundLast = false;
  for (var i = arr.length - 1; i >= 0; i--) {
    var n = _healthMetricFinite(arr[i]);
    if (n === null) continue;
    if (!foundLast) {
      foundLast = true;
      continue;
    }
    return n;
  }
  return null;
}

function _healthMetricsTrend(values, lowerIsBetter) {
  var last = _healthMetricsLastFinite(values);
  var prev = _healthMetricsPreviousFinite(values);
  if (last === null || prev === null) return 'trend —';
  var diff = last - prev;
  if (Math.abs(diff) < 0.0001) return 'flat vs prev';
  var pct = prev === 0 ? null : Math.abs(diff / prev * 100);
  var direction = diff > 0 ? '↑' : '↓';
  var better = lowerIsBetter ? diff < 0 : diff > 0;
  var label = direction + ' ' + (pct === null ? _healthFormatNumber(Math.abs(diff), 1) : _healthFormatNumber(pct, 0) + '%')
    + ' vs prev';
  return better ? label + ' better' : label;
}

function _healthMetricsTickDetail() {
  var tick = healthMetricsState.tick;
  if (!tick) return 'waiting for metrics_tick';
  if (tick.enabled === false) return 'metrics off';
  if (tick.interval_ms) return 'tick ' + Math.round(Number(tick.interval_ms) || 0) + 'ms';
  return 'live tick';
}

function _healthMetricsCard(title, value, detail, series, className) {
  return _healthSummaryCard(
    title,
    value,
    detail,
    series && series.length ? series : null,
    'health-metrics-card ' + (className || '')
  );
}

function _healthMetricsLiveHtml() {
  var perf = _healthMetricsTickPerf();
  var tickDetail = _healthMetricsTickDetail();
  var lag = perf ? (perf.event_loop_lag_ms || {}) : {};
  var wsPerf = perf ? (perf.ws || {}) : {};
  var db = perf ? (perf.db || {}) : {};
  var proc = perf ? (perf.proc || {}) : {};
  var live = perf ? (perf.live || {}) : {};
  var frontend = perf && perf.frontend && typeof perf.frontend === 'object'
    ? perf.frontend
    : null;
  var overhead = (healthMetricsState.tick && healthMetricsState.tick.meter_overhead) || {};

  var lagSeries = _healthMetricsSeries('event_loop_lag_p95_ms');
  var wsSeries = _healthMetricsSeries('ws_deltas_per_s');
  var dbSeries = _healthMetricsSeries('db_write_latency_p95_ms');
  var rssSeries = _healthMetricsSeries('rss_mb');
  var cpuSeries = _healthMetricsSeries('cpu_pct');
  var frontendRateSeries = _healthMetricsSeries(['frontend_render_per_s', 'render_per_s']);
  var frontendMsSeries = _healthMetricsSeries(['frontend_render_ms_p95', 'render_ms_p95']);
  var liveSeries = _healthMetricsSeries(['live_agents', 'agents', 'live_agent_count']);

  var cards = [
    _healthMetricsCard(
      'Event-loop lag',
      _healthMetricFormat(lag.p95, 1, 'ms'),
      'p50 ' + _healthMetricFormat(lag.p50, 1, 'ms')
        + ' · max ' + _healthMetricFormat(lag.max, 1, 'ms')
        + ' · ' + _healthMetricsTrend(lagSeries, true),
      lagSeries,
      'health-card-lag'
    ),
    _healthMetricsCard(
      'WS throughput',
      _healthMetricFormatRate(wsPerf.deltas_per_s, 1, '/s'),
      _healthMetricFormatRate(wsPerf.bytes_per_s, 0, ' B/s')
        + ' · subs ' + _healthMetricFormat(wsPerf.subscribers, 0, '')
        + ' · ' + _healthMetricsTrend(wsSeries, false),
      wsSeries,
      'health-card-ws'
    ),
    _healthMetricsCard(
      'DB writes',
      _healthMetricFormat(db.write_latency_p95_ms, 1, 'ms'),
      _healthMetricFormatRate(db.writes_per_s, 1, '/s')
        + ' writes · ' + _healthMetricsTrend(dbSeries, true),
      dbSeries,
      'health-card-db'
    ),
    _healthMetricsCard(
      'Process memory',
      _healthMetricFormat(proc.rss_mb, 0, 'MB'),
      'cpu ' + _healthMetricFormat(proc.cpu_pct, 1, '%')
        + ' · mem ' + _healthMetricsTrend(rssSeries, true),
      rssSeries,
      'health-card-mem'
    ),
    _healthMetricsCard(
      'Process CPU',
      _healthMetricFormat(proc.cpu_pct, 1, '%'),
      'rss ' + _healthMetricFormat(proc.rss_mb, 0, 'MB')
        + ' · cpu ' + _healthMetricsTrend(cpuSeries, true),
      cpuSeries,
      'health-card-cpu'
    ),
    _healthMetricsCard(
      'Live counts',
      _healthMetricFormat(live.agents, 0, ' agents'),
      _healthMetricFormat(live.ptys, 0, ' ptys')
        + ' · queue ' + _healthMetricFormat(live.prompt_queue_depth, 0, '')
        + ' · ' + _healthMetricsTrend(liveSeries, false),
      liveSeries,
      'health-card-live'
    ),
    _healthMetricsCard(
      'Supervisor link',
      (live.supervisor_connected === undefined
        ? '—'
        : (live.supervisor_connected ? 'connected' : 'disconnected')),
      (live.supervisor_latency_ms != null
        ? _healthMetricFormat(live.supervisor_latency_ms, 1, 'ms')
        : '—')
        + ' · ' + _healthMetricFormat(live.stuck_sessions, 0, ' stuck'),
      null,
      'health-card-supervisor'
    ),
    _healthMetricsCard(
      'Frontend renders',
      frontend ? _healthMetricFormatRate(frontend.render_per_s, 1, '/s') : '—',
      frontend
        ? ('p95 ' + _healthMetricFormat(frontend.render_ms_p95, 1, 'ms')
          + ' · ' + _healthMetricsTrend(frontendRateSeries || frontendMsSeries, true))
        : 'frontend not reporting',
      frontendRateSeries || frontendMsSeries,
      'health-card-frontend'
    ),
  ].join('');

  var overheadHtml = '<div class="health-metrics-overhead">'
    + '<span>' + _healthEsc(tickDetail) + '</span>'
    + '<span>meter ' + _healthMetricFormat(overhead.agg_tick_ms, 2, 'ms') + '</span>'
    + '<span>overhead ' + _healthMetricFormat(overhead.collect_overhead_pct, 2, '%') + '</span>'
    + '</div>';
  return '<div class="health-card-grid health-metrics-grid">' + cards + '</div>' + overheadHtml;
}

function _healthMetricsHistoryHtml() {
  if (healthMetricsState.historyError) {
    return '<div class="health-error">' + _healthEsc(healthMetricsState.historyError) + '</div>';
  }
  if (healthMetricsState.historyLoading && !healthMetricsState.historyPayload) {
    return '<div class="health-loading">Loading metrics history…</div>';
  }
  var payload = healthMetricsState.historyPayload;
  if (!payload) {
    return '<div class="health-empty">Open the panel to load over-time metrics history.</div>';
  }
  var bucketLabel = (payload.bucket_seconds || 0) < 86400 ? 'hourly' : 'daily';
  var retention = (payload.perf && payload.perf.retention) || {};
  var notes = Array.isArray(payload.notes) ? payload.notes : [];
  var html = '<div class="health-metrics-history-meta">'
    + '<span>' + _healthEsc(payload.window || healthState.window) + ' · ' + _healthEsc(bucketLabel) + ' metrics history</span>'
    + '<span>' + _healthEsc(payload.group || 'All groups') + '</span>';
  if (retention.kept_seconds) {
    html += '<span>retention ' + _healthEsc(_healthFormatDuration(retention.kept_seconds)) + '</span>';
  }
  html += '</div>';
  if (notes.length) {
    html += '<ul class="health-notes health-metrics-notes">' + notes.map(function(note) {
      return '<li>' + _healthEsc(note) + '</li>';
    }).join('') + '</ul>';
  }
  if (healthMetricsState.historyLoading) {
    html += '<div class="health-metrics-refreshing">refreshing…</div>';
  }
  return html;
}

function _healthMetricsSectionHtml() {
  var open = healthMetricsState.expanded ? ' open' : '';
  return '<details id="health-metrics-details" class="health-section health-metrics-section"'
    + ' data-health-metrics-section' + open + ' onchange="healthMetricsSetExpanded(this.open)">'
    + '<summary class="health-metrics-summary">'
    + '<span class="health-metrics-title">Runtime metrics</span>'
    + '<span class="health-metrics-summary-status">' + _healthEsc(_healthMetricsStatusText()) + '</span>'
    + '</summary>'
    + '<div id="health-metrics-live" class="health-metrics-live">' + _healthMetricsLiveHtml() + '</div>'
    + '<div id="health-metrics-history" class="health-metrics-history">' + _healthMetricsHistoryHtml() + '</div>'
    + '</details>';
}

function _healthMetricsCaptureExpandedFromDom() {
  var details = document.getElementById('health-metrics-details');
  if (details && typeof details.open !== 'undefined') {
    healthMetricsState.expanded = !!details.open;
  }
}

function _healthMetricsUpdateSectionDom() {
  _healthMetricsCaptureExpandedFromDom();
  var details = document.getElementById('health-metrics-details');
  if (!details) return false;
  var scrollParent = document.getElementById('panel-health');
  var scrollTop = scrollParent ? scrollParent.scrollTop : 0;
  var live = document.getElementById('health-metrics-live');
  var history = document.getElementById('health-metrics-history');
  var status = null;
  if (typeof details.querySelector === 'function') {
    status = details.querySelector('.health-metrics-summary-status');
  }
  if (typeof details.open !== 'undefined') details.open = !!healthMetricsState.expanded;
  if (status) status.textContent = _healthMetricsStatusText();
  if (live) live.innerHTML = _healthMetricsLiveHtml();
  if (history) history.innerHTML = _healthMetricsHistoryHtml();
  if (scrollParent) scrollParent.scrollTop = scrollTop;
  return true;
}

function _healthMetricsHistoryCacheKey() {
  return healthState.window + '::' + _healthRequestGroup();
}

function _healthMetricsResetHistory() {
  healthMetricsState.historyPayload = null;
  healthMetricsState.historyError = '';
  healthMetricsState.historyLoadedKey = '';
}

function _healthMetricsScheduleHistoryRefresh() {
  if (healthMetricsState.historyRefreshTimer && typeof clearTimeout === 'function') {
    clearTimeout(healthMetricsState.historyRefreshTimer);
  }
  if (typeof setTimeout !== 'function') return;
  healthMetricsState.historyRefreshTimer = setTimeout(function() {
    healthMetricsState.historyRefreshTimer = 0;
    if (_healthVisible()) healthMetricsEnsureHistoryLoaded({ force: true });
  }, HEALTH_METRICS_HISTORY_REFRESH_MS);
}

function healthMetricsEnsureHistoryLoaded(opts) {
  opts = opts || {};
  if (!opts.force && !_healthVisible()) return;
  var key = _healthMetricsHistoryCacheKey();
  var now = _healthMetricsNowMs();
  if (!opts.force && healthMetricsState.historyLoadedKey === key && healthMetricsState.historyPayload) {
    if (now - (healthMetricsState.historyLastRequestedAt || 0) < HEALTH_METRICS_HISTORY_REFRESH_MS) return;
  }
  if (healthMetricsState.historyLoading
      && healthMetricsState.historyRequestedKey === key
      && !opts.force) return;
  healthMetricsState.historyLoading = true;
  healthMetricsState.historyError = '';
  healthMetricsState.historyRequestedKey = key;
  healthMetricsState.historyLastRequestedAt = now;
  _healthMetricsUpdateSectionDom();
  if (typeof send === 'function') {
    send({
      cmd: 'get_metrics_history',
      window: healthState.window,
      group: _healthRequestGroup(),
    });
  }
  _healthMetricsScheduleHistoryRefresh();
}

function healthMetricsReceiveHistory(msg) {
  var incomingKey = '';
  if (msg && msg.type !== 'error') {
    incomingKey = (msg.window ? msg.window : healthState.window)
      + '::' + (msg.group || '');
    if (_healthRequestGroup()
        && incomingKey
        && incomingKey !== _healthMetricsHistoryCacheKey()
        && incomingKey !== healthMetricsState.historyRequestedKey) {
      return;
    }
  }
  healthMetricsState.historyLoading = false;
  if (msg && msg.type === 'error') {
    healthMetricsState.historyError = msg.message || 'Unable to load metrics history.';
  } else {
    healthMetricsState.historyError = '';
    healthMetricsState.historyPayload = msg || null;
    healthMetricsState.historyLoadedKey = healthMetricsState.historyRequestedKey || incomingKey || ((msg && msg.window ? msg.window : healthState.window)
      + '::' + ((msg && msg.group) || ''));
  }
  if (!_healthMetricsUpdateSectionDom()) _healthUpdateResults();
  if (_healthVisible()) _healthMetricsScheduleHistoryRefresh();
}

function healthMetricsReceiveTick(msg) {
  if (!msg || typeof msg !== 'object') return;
  healthMetricsState.tick = msg;
  healthMetricsState.tickSeenAt = _healthMetricsNowMs();
  if (_healthVisible()) _healthMetricsUpdateSectionDom();
  if (typeof refreshStatusBar === 'function') refreshStatusBar({ metrics: true });
}

function healthMetricsSetExpanded(open) {
  healthMetricsState.expanded = !!open;
}

function healthOpenMetrics() {
  healthMetricsState.expanded = true;
  if (typeof _healthVisible === 'function' && !_healthVisible()
      && typeof togglePanel === 'function') {
    togglePanel('health');
  } else if (typeof renderHealthPanel === 'function') {
    renderHealthPanel();
  }
  var details = document.getElementById('health-metrics-details');
  if (details && typeof details.open !== 'undefined') details.open = true;
  if (details && typeof details.scrollIntoView === 'function') {
    details.scrollIntoView({ block: 'nearest' });
  }
}

function healthMetricsGetStatusBarView() {
  var tick = healthMetricsState.tick;
  if (!tick) {
    return {
      visible: true,
      label: 'Metrics —',
      level: 'unknown',
      title: 'Runtime metrics tick has not arrived yet.',
    };
  }
  if (tick.enabled === false) {
    return {
      visible: true,
      label: 'Metrics off',
      level: 'muted',
      title: 'Runtime metrics collection is disabled.',
    };
  }
  var perf = (tick.perf && typeof tick.perf === 'object') ? tick.perf : {};
  var lag = perf.event_loop_lag_ms || {};
  var proc = perf.proc || {};
  var frontend = perf.frontend && typeof perf.frontend === 'object' ? perf.frontend : null;
  var lagP95 = _healthMetricFinite(lag.p95);
  var cpu = _healthMetricFinite(proc.cpu_pct);
  var level = 'normal';
  if ((lagP95 !== null && lagP95 >= 100) || (cpu !== null && cpu >= 90)) level = 'danger';
  else if ((lagP95 !== null && lagP95 >= 50) || (cpu !== null && cpu >= 75)) level = 'warn';
  var title = 'Runtime metrics'
    + '\nEvent-loop lag p95: ' + _healthMetricFormat(lag.p95, 1, 'ms')
    + '\nRSS: ' + _healthMetricFormat(proc.rss_mb, 0, 'MB')
    + '\nCPU: ' + _healthMetricFormat(proc.cpu_pct, 1, '%');
  if (frontend) {
    title += '\nFrontend renders: ' + _healthMetricFormatRate(frontend.render_per_s, 1, '/s')
      + ' · p95 ' + _healthMetricFormat(frontend.render_ms_p95, 1, 'ms');
  } else {
    title += '\nFrontend renders: —';
  }
  return {
    visible: true,
    label: 'Lag ' + _healthMetricFormat(lag.p95, 1, 'ms')
      + ' · Mem ' + _healthMetricFormat(proc.rss_mb, 0, 'MB'),
    level: level,
    title: title,
  };
}

function _healthFrontendRenderNow() {
  if (typeof performance !== 'undefined' && performance && typeof performance.now === 'function') {
    return performance.now();
  }
  return _healthMetricsNowMs();
}

function _healthPruneFrontendSamples(now) {
  var cutoff = now - HEALTH_FRONTEND_RENDER_WINDOW_MS;
  var samples = healthMetricsState.frontendSamples || [];
  var kept = [];
  for (var i = 0; i < samples.length; i++) {
    if (Number(samples[i].t || 0) >= cutoff) kept.push(samples[i]);
  }
  if (kept.length > HEALTH_FRONTEND_RENDER_SAMPLE_LIMIT) {
    kept = kept.slice(kept.length - HEALTH_FRONTEND_RENDER_SAMPLE_LIMIT);
  }
  healthMetricsState.frontendSamples = kept;
  return kept;
}

function healthRecordFrontendRender(durationMs, source) {
  var now = _healthMetricsNowMs();
  var duration = _healthMetricFinite(durationMs);
  if (duration === null || duration < 0) duration = 0;
  healthMetricsState.frontendEverSampled = true;
  healthMetricsState.frontendSamples.push({
    t: now,
    duration_ms: duration,
    source: String(source || ''),
  });
  _healthPruneFrontendSamples(now);
}

function _healthFrontendRenderStats(now) {
  var samples = _healthPruneFrontendSamples(now);
  if (!samples.length) {
    return { render_per_s: 0, render_ms_p95: 0 };
  }
  var first = Number(samples[0].t || now);
  var last = Number(samples[samples.length - 1].t || now);
  var spanSeconds = Math.max(1, (Math.max(now, last) - first) / 1000);
  var durations = samples.map(function(sample) {
    var n = _healthMetricFinite(sample.duration_ms);
    return n === null || n < 0 ? 0 : n;
  }).sort(function(a, b) { return a - b; });
  var idx = Math.max(0, Math.min(durations.length - 1, Math.ceil(durations.length * 0.95) - 1));
  return {
    render_per_s: samples.length / spanSeconds,
    render_ms_p95: durations[idx] || 0,
  };
}

function healthReportFrontendRender(opts) {
  opts = opts || {};
  if (!healthMetricsState.frontendEverSampled) return false;
  if (typeof send !== 'function') return false;
  var now = _healthMetricsNowMs();
  var stats = _healthFrontendRenderStats(now);
  send({
    cmd: 'report_frontend_render',
    render_per_s: stats.render_per_s,
    render_ms_p95: stats.render_ms_p95,
  });
  return true;
}

function _healthScheduleFrontendRenderReport() {
  if (healthMetricsState.frontendReportTimer && typeof clearTimeout === 'function') {
    clearTimeout(healthMetricsState.frontendReportTimer);
  }
  if (typeof setTimeout !== 'function') return;
  healthMetricsState.frontendReportTimer = setTimeout(function() {
    healthMetricsState.frontendReportTimer = 0;
    healthReportFrontendRender({ timer: true });
    _healthScheduleFrontendRenderReport();
  }, HEALTH_FRONTEND_RENDER_REPORT_MS);
}

function healthStartFrontendRenderReporting() {
  _healthScheduleFrontendRenderReport();
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
  var metricsHtml = _healthMetricsSectionHtml();
  if (healthState.error) {
    return metricsHtml + '<div class="health-error">' + _healthEsc(healthState.error) + '</div>';
  }
  if (healthState.loading && !payload) {
    return metricsHtml + '<div class="health-loading">Loading health metrics…</div>';
  }
  if (!payload) {
    return metricsHtml + '<div class="health-empty">Open the panel to load health metrics.</div>';
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
  return metricsHtml
    + '<div class="health-meta">'
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
  if (!opts.skipEnsure) {
    healthEnsureLoaded();
    healthMetricsEnsureHistoryLoaded();
  }
}

function _healthUpdateResults() {
  var root = document.getElementById('panel-health');
  var results = document.getElementById('health-results');
  if (!results) return;
  _healthMetricsCaptureExpandedFromDom();
  var scrollTop = root ? root.scrollTop : 0;
  var surfaceState = (typeof _captureSurfaceState === 'function')
    ? _captureSurfaceState(results)
    : null;
  results.innerHTML = _healthResultsHtml(healthState.payload);
  if (surfaceState && typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(results, surfaceState);
  }
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
  _healthMetricsResetHistory();
  renderHealthPanel({ skipEnsure: true });
  healthEnsureLoaded({ force: true });
  healthMetricsEnsureHistoryLoaded({ force: true });
}

function healthSetScope(value) {
  var next = value === 'all' ? 'all' : 'active';
  if (healthState.scope === next) return;
  healthState.scope = next;
  healthState.payload = null;
  _healthMetricsResetHistory();
  renderHealthPanel({ skipEnsure: true });
  healthEnsureLoaded({ force: true });
  healthMetricsEnsureHistoryLoaded({ force: true });
}

function healthRefresh() {
  healthEnsureLoaded({ force: true });
  healthMetricsEnsureHistoryLoaded({ force: true });
}

function healthActiveGroupChanged() {
  _healthSyncControls();
  if (healthState.scope !== 'active') return;
  healthState.payload = null;
  healthState.loadedKey = '';
  _healthMetricsResetHistory();
  if (_healthVisible()) {
    healthEnsureLoaded({ force: true });
    healthMetricsEnsureHistoryLoaded({ force: true });
  } else {
    _healthUpdateResults();
  }
}

healthStartFrontendRenderReporting();
