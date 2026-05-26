function _renderAgentGroupTabsHost(tabsHtml) {
  const host = _agentGroupTabsHost();
  if (!host) return false;
  const nextHtml = tabsHtml || '';
  if (host._torqueLastHtml === nextHtml && host.innerHTML === nextHtml) return false;
  host.innerHTML = nextHtml;
  host._torqueLastHtml = nextHtml;
  return true;
}

function _renderAgentGroupTabsHtml() {
  if (!_singleGroupModeEnabled()) return '';
  const groups = _groupNamesSorted();
  const active = _activeGroup() || '';
  let html = '<div class="agent-group-tabs" data-agent-group-tabs>';
  html += '<div class="agent-group-tabs-list" role="tablist" aria-label="Groups">';
  if (!groups.length) {
    html += '<span class="agent-group-tabs-empty">No groups</span>';
  }
  for (const group of groups) {
    const selected = group === active;
    const count = ((state.groups || {})[group] || []).length;
    const groupArg = _jsStringAttr(group);
    html += '<div'
      + ' class="agent-group-tab' + (selected ? ' active' : '') + '"'
      + ' role="tab"'
      + ' tabindex="0"'
      + ' aria-selected="' + (selected ? 'true' : 'false') + '"'
      + ' title="' + esc(group) + '"'
      + ' onclick="onGroupTabClick(' + groupArg + ', event)"'
      + ' onkeydown="if(event.key===\'Enter\'||event.key===\' \'){onGroupTabClick(' + groupArg + ', event)}"'
      + ' oncontextmenu="onGroupTabContextMenu(event, ' + groupArg + ')">'
      + '<span class="agent-group-tab-name">' + esc(group) + '</span>'
      + '<span class="agent-group-tab-count">' + count + '</span>'
      + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-group-tab-actions">';
  if (typeof _torqueAgentViewMode === 'function') {
    const vm = _torqueAgentViewMode();
    const gridIcon = '<svg viewBox="0 0 12 12" width="12" height="12" fill="currentColor" aria-hidden="true">'
      + '<rect x="0" y="0" width="5" height="5" rx="1"/>'
      + '<rect x="7" y="0" width="5" height="5" rx="1"/>'
      + '<rect x="0" y="7" width="5" height="5" rx="1"/>'
      + '<rect x="7" y="7" width="5" height="5" rx="1"/>'
      + '</svg>';
    const canvasIcon = '<svg viewBox="0 0 12 12" width="12" height="12" fill="currentColor" aria-hidden="true">'
      + '<rect x="0" y="1" width="12" height="2" rx="0.5"/>'
      + '<rect x="3" y="5" width="9" height="2" rx="0.5"/>'
      + '<rect x="6" y="9" width="6" height="2" rx="0.5"/>'
      + '</svg>';
    html += '<div class="agent-view-toggle agent-view-toggle--tabs" role="group" aria-label="Agent view">';
    html += '<button type="button" class="agent-view-toggle-btn' + (vm === 'grid' ? ' is-active' : '') + '"'
      + ' data-agent-view-toggle="grid"'
      + ' onclick="_torqueSetAgentViewMode(\'grid\')" title="Grid view" aria-label="Grid view">' + gridIcon + '</button>';
    html += '<button type="button" class="agent-view-toggle-btn' + (vm === 'canvas' ? ' is-active' : '') + '"'
      + ' data-agent-view-toggle="canvas"'
      + ' onclick="_torqueSetAgentViewMode(\'canvas\')" title="Canvas view (tree)" aria-label="Canvas view">' + canvasIcon + '</button>';
    html += '</div>';
  }
  html += '<button type="button" class="agent-group-tab-action agent-group-tab-action-new"'
    + ' onclick="openAddGroup()">+ New Group</button>';
  html += '<button type="button" class="agent-group-tab-action agent-group-tab-action-settings"'
    + (active ? '' : ' disabled')
    + ' title="Group settings" aria-label="Group settings"'
    + ' onclick="openActiveGroupSettings(event)">&#9881;</button>';
  html += '</div>';
  html += '</div>';
  return html;
}

