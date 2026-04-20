/* Agent panel — focused-agent router skeleton */

var _agentPanelLastSelectedTabByKind = {};

function _agentPanelEsc(value) {
  if (typeof _esc === 'function') return _esc(value);
  if (typeof esc === 'function') return esc(value);
  return String(value);
}

function _agentPanelKind(agent) {
  if (!agent) return '';
  if ((agent.cell_type || '') === 'terminal') return 'terminal';
  var kind = String(agent.kind || '').trim();
  if (kind === 'architect' || kind === 'engineer' || kind === 'worker') return kind;
  return 'worker';
}

function _resolveFocusedAgent() {
  if (typeof focusedItemId === 'undefined' || !focusedItemId) return null;
  if (!state || !state.agents) return null;
  return state.agents[focusedItemId] || null;
}

function _agentPanelSelectedTab(kind) {
  kind = String(kind || '').trim();
  if (!kind) return '';
  return _agentPanelLastSelectedTabByKind[kind] || '';
}

function agentPanelSelectTab(tab) {
  var agent = _resolveFocusedAgent();
  if (!agent) return;
  var kind = _agentPanelKind(agent);
  if (!kind) return;
  _agentPanelLastSelectedTabByKind[kind] = String(tab || '');
  renderAgentPanel();
}

function _renderAgentPanelStub(agent) {
  var kind = _agentPanelKind(agent);
  var name = String((agent && (agent.name || agent.id)) || 'Unknown');
  var selectedTab = _agentPanelSelectedTab(kind);
  var tabAttr = selectedTab
    ? ' data-agent-panel-tab="' + _agentPanelEsc(selectedTab) + '"'
    : '';
  var html = '<div class="agent-panel"' + tabAttr + '>';
  html += '<div class="agent-panel-header">Agent: '
    + _agentPanelEsc(name)
    + ' · Kind: '
    + _agentPanelEsc(kind)
    + '</div>';
  html += '<div class="agent-panel-empty">Coming in stage 4</div>';
  html += '</div>';
  return html;
}

function _renderArchitectPanel(agent) {
  return _renderAgentPanelStub(agent);
}

function _renderEngineerPanel(agent) {
  return _renderAgentPanelStub(agent);
}

function _renderWorkerPanel(agent) {
  return _renderAgentPanelStub(agent);
}

function _renderTerminalPanel(agent) {
  return _renderAgentPanelStub(agent);
}

function renderAgentPanel() {
  if (typeof _weaverStopEventsCountdownTimer === 'function') {
    _weaverStopEventsCountdownTimer();
  }
  var el = document.getElementById('panel-agent');
  if (!el) return;

  var panelStateOptions = {
    scrollSelectors: [':root'],
  };
  if (typeof _captureMainFocusKey === 'function') {
    panelStateOptions.captureFocusKey = _captureMainFocusKey;
  }

  var panelState = typeof _captureSurfaceState === 'function'
    ? _captureSurfaceState(el, panelStateOptions)
    : null;
  var agent = _resolveFocusedAgent();
  var html = '';

  if (!agent) {
    html = '<div class="agent-panel">'
      + '<div class="agent-panel-empty">Select an agent from the grid to see its context.</div>'
      + '</div>';
  } else {
    switch (_agentPanelKind(agent)) {
      case 'architect':
        html = _renderArchitectPanel(agent);
        break;
      case 'engineer':
        html = _renderEngineerPanel(agent);
        break;
      case 'terminal':
        html = _renderTerminalPanel(agent);
        break;
      case 'worker':
      default:
        html = _renderWorkerPanel(agent);
        break;
    }
  }

  el.innerHTML = html;
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(el, panelState, panelStateOptions);
  }
}
