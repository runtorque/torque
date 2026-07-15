/* Agent panel module: hierarchy. */

function _agentPanelHierarchyAgentItem(agent, role, current) {
  role = String(role || _agentPanelKind(agent) || '').trim();
  var item = {
    role: role,
    label: _agentPanelAgentDisplayName(agent, 'Unknown ' + role),
    current: !!current,
  };
  if (agent && agent.id) item.agentId = String(agent.id);
  if (agent && agent.group) item.group = String(agent.group);
  return item;
}

function _agentPanelHierarchyBreadcrumb(items) {
  items = Array.isArray(items) ? items : [];
  if (!items.length) return '';
  var html = '<div class="agent-panel-hierarchy-breadcrumb" aria-label="Agent hierarchy">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};
    if (i > 0) {
      html += '<span class="agent-panel-hierarchy-arrow" aria-hidden="true">\u203A</span>';
    }
    var role = String(item.role || '').trim();
    var crumbClass = 'agent-panel-hierarchy-crumb';
    if (role) crumbClass += ' agent-panel-hierarchy-crumb-' + _agentPanelAttr(role);
    if (item.current) crumbClass += ' current';
    if (item.missing) crumbClass += ' missing';
    var clickTarget = !item.current && !item.missing
      && (String(item.agentId || '').trim() || String(item.principal || '').trim());
    var tag = clickTarget ? 'button' : 'span';
    html += '<' + tag + (clickTarget ? ' type="button"' : '') + ' class="' + crumbClass + '"';
    if (clickTarget) {
      var targetAgentId = String(item.agentId || '');
      var targetKind = String(item.principal || role || '');
      var targetGroup = String(item.group || '');
      html += ' onclick="' + _agentPanelEventAttr(
        'event.stopPropagation();agentPanelFocusHierarchyTarget('
          + JSON.stringify(targetAgentId) + ','
          + JSON.stringify(targetKind) + ','
          + JSON.stringify(targetGroup)
          + ')'
      ) + '"';
    }
    html += '>';
    if (role) {
      html += '<span class="agent-panel-hierarchy-role">' + _agentPanelEsc(_agentPanelHierarchyRoleLabel(role)) + '</span>';
    }
    html += '<span class="agent-panel-hierarchy-name">' + _agentPanelEsc(item.label || '') + '</span>';
    if (item.count !== '' && typeof item.count !== 'undefined' && item.count !== null) {
      html += '<span class="agent-panel-hierarchy-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + _agentPanelEsc(String(item.count)) + '</span>';
    }
    html += '</' + tag + '>';
  }
  html += '</div>';
  return html;
}

function _agentPanelHierarchyRoleLabel(role) {
  role = String(role || '').trim();
  if (role === 'architect') return 'ARCH';
  if (role === 'engineer') return 'ENGINEER';
  if (role === 'worker') return 'WORKER';
  if (role === 'user') return 'USER';
  return role;
}

function _agentPanelHierarchyThemeClass(levelClass, workerLevelClass) {
  var classes = String(levelClass || '') + ' ' + String(workerLevelClass || '');
  if (classes.indexOf('architect-') !== -1) return 'agent-panel-hierarchy-branch-architect';
  if (classes.indexOf('user-') !== -1 || classes.indexOf('engineer-roster-') !== -1) {
    return 'agent-panel-hierarchy-branch-user';
  }
  return 'agent-panel-hierarchy-branch-generic';
}

function _agentPanelHierarchyUnknownItem(role) {
  role = String(role || 'agent').trim();
  return {
    role: role,
    label: 'Unknown ' + role,
    current: false,
    missing: true,
  };
}

function _agentPanelHierarchyUserItem(group, current) {
  group = String(group || '').trim();
  return {
    role: 'user',
    label: 'User',
    current: !!current,
    group: group,
    principal: 'user',
  };
}

function _agentPanelHierarchyWorkerCount(group, engineers) {
  var count = 0;
  engineers = Array.isArray(engineers) ? engineers : [];
  if (typeof _engineerWorkerAgents !== 'function') return count;
  for (var i = 0; i < engineers.length; i++) {
    var engineer = engineers[i];
    if (!engineer) continue;
    count += _engineerWorkerAgents(group, engineer.id).length;
  }
  return count;
}

function _agentPanelUpwardBreadcrumbHtml(agent) {
  return _agentPanelHierarchyBreadcrumb(_agentPanelUpwardChain(agent));
}

function _agentPanelUpwardChain(agent) {
  var kind = _agentPanelKind(agent);
  var group = String((agent && agent.group) || '');
  if (kind === 'architect') {
    return [_agentPanelHierarchyAgentItem(agent, 'architect', true)];
  }
  if (kind === 'user') {
    return [_agentPanelHierarchyUserItem(group, true)];
  }
  if (kind === 'engineer') {
    var architectId = String((agent && agent.hired_by_architect_id) || '').trim();
    var items = [];
    if (architectId) {
      var architect = state && state.agents ? state.agents[architectId] : null;
      items.push(architect
        ? _agentPanelHierarchyAgentItem(architect, 'architect', false)
        : _agentPanelHierarchyUnknownItem('architect'));
    } else {
      items.push(_agentPanelHierarchyUserItem(group, false));
    }
    items.push(_agentPanelHierarchyAgentItem(agent, 'engineer', true));
    return items;
  }
  var ownerEngineerId = String(
    (agent && (agent.owner_engineer_id || agent.created_by_engineer_id)) || ''
  ).trim();
  var engineer = ownerEngineerId && state && state.agents ? state.agents[ownerEngineerId] : null;
  var chain = [];
  if (engineer) {
    var parentArchitectId = String(engineer.hired_by_architect_id || '').trim();
    if (parentArchitectId) {
      var parentArchitect = state && state.agents ? state.agents[parentArchitectId] : null;
      chain.push(parentArchitect
        ? _agentPanelHierarchyAgentItem(parentArchitect, 'architect', false)
        : _agentPanelHierarchyUnknownItem('architect'));
    } else {
      chain.push(_agentPanelHierarchyUserItem(group || engineer.group || '', false));
    }
    chain.push(_agentPanelHierarchyAgentItem(engineer, 'engineer', false));
  } else if (ownerEngineerId) {
    chain.push(_agentPanelHierarchyUnknownItem('engineer'));
  } else {
    chain.push(_agentPanelHierarchyUserItem(group, false));
  }
  chain.push(_agentPanelHierarchyAgentItem(agent, 'worker', true));
  return chain;
}

function agentPanelFocusHierarchyTarget(agentId, kind, group) {
  kind = String(kind || '').trim();
  group = String(group || '').trim();
  if (kind === 'user') {
    if (typeof selectPrincipal === 'function') {
      selectPrincipal('', group);
      if (typeof renderAgentPanel === 'function') renderAgentPanel();
    } else {
      focusedItemId = 'principal:' + group + ':user';
      if (typeof renderAgentPanel === 'function') renderAgentPanel();
      else if (typeof render === 'function') render();
    }
    return;
  }

  agentId = String(agentId || '').trim();
  if (!agentId) return;
  if (typeof focusAgent === 'function') {
    focusAgent(agentId);
  } else {
    focusedItemId = agentId;
    if (typeof send === 'function') send({ cmd: 'focus_agent', id: agentId });
  }
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
  else if (typeof render === 'function') render();
}
