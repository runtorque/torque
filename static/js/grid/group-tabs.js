function _renderAgentGroupTabsHost(tabsHtml) {
  const host = _agentGroupTabsHost();
  if (!host) return false;
  const nextHtml = tabsHtml || '';
  if (host._torqueLastHtml === nextHtml && host.innerHTML === nextHtml) return false;
  host.innerHTML = nextHtml;
  host._torqueLastHtml = nextHtml;
  const revealActive = function() {
    const active = host.querySelector && host.querySelector('.agent-group-tab.active');
    if (active && typeof active.scrollIntoView === 'function') {
      active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(revealActive);
  else revealActive();
  return true;
}

function _renderAgentGroupTabsHtml() {
  if (!_singleGroupModeEnabled()) return '';
  const groups = _groupNamesSorted();
  const active = _activeGroup() || '';
  let html = '<div class="agent-group-tabs" data-agent-group-tabs>';
  html += '<div class="agent-group-tabs-list" role="tablist" aria-label="Groups"'
    + ' onwheel="_scrollAgentGroupTabs(event)">';
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
      + ' tabindex="' + (selected ? '0' : '-1') + '"'
      + ' aria-selected="' + (selected ? 'true' : 'false') + '"'
      + ' title="' + esc(group) + '"'
      + ' onclick="onGroupTabClick(' + groupArg + ', event)"'
      + ' onkeydown="agentGroupTabKeydown(event,' + groupArg + ')"'
      + ' oncontextmenu="onGroupTabContextMenu(event, ' + groupArg + ')">'
      + '<span class="agent-group-tab-name">' + esc(group) + '</span>'
      + '<span class="agent-group-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count" aria-label="' + count + ' agents">' + count + '</span>'
      + (selected
        ? '<button type="button" class="agent-group-tab-menu" title="Group actions" aria-label="Group actions for ' + esc(group) + '"'
          + ' aria-haspopup="menu" aria-expanded="false"'
          + ' onclick="openAgentGroupTabActions(event,' + groupArg + ')">'
          + '<span aria-hidden="true">&#8943;</span></button>'
        : '')
      + '</div>';
  }
  html += '</div>';

  const activeCount = active ? ((state.groups || {})[active] || []).length : 0;
  html += '<div class="agent-group-compact">';
  html += '<button type="button" class="agent-group-compact-trigger" aria-haspopup="dialog" aria-expanded="false"'
    + ' title="Switch group (⌘G / Ctrl+G)" onclick="toggleAgentGroupQuickSwitcher(event)">'
    + '<span class="agent-group-compact-name">' + esc(active || 'Choose group') + '</span>'
    + (active ? '<span class="agent-group-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + activeCount + '</span>' : '')
    + '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="m3 4.5 3 3 3-3"/></svg>'
    + '</button>';
  html += '<div class="agent-group-quick-switcher ui-popover" role="dialog" aria-label="Switch group" hidden'
    + ' onclick="event.stopPropagation()">'
    + '<input class="agent-group-quick-search" type="search" placeholder="Find a group…"'
    + ' aria-label="Find a group" oninput="filterAgentGroupQuickSwitcher(this.value)"'
    + ' onkeydown="agentGroupQuickSwitcherKeydown(event)">'
    + '<div class="agent-group-quick-results">';
  for (const group of groups) {
    const selected = group === active;
    const count = ((state.groups || {})[group] || []).length;
    const groupArg = _jsStringAttr(group);
    html += '<button type="button" class="agent-group-quick-option ui-menu-item' + (selected ? ' active is-selected' : '') + '"'
      + ' data-group-switch-option data-search="' + esc(group.toLowerCase()) + '"'
      + ' onclick="selectAgentGroupFromQuickSwitcher(' + groupArg + ')">'
      + '<span>' + esc(group) + '</span><span class="agent-group-quick-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + count + ' agents</span>'
      + (selected ? '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="m2.5 6.2 2.1 2.1 4.9-5"/></svg>' : '')
      + '</button>';
  }
  html += '</div>';
  html += '<button type="button" class="agent-group-quick-new ui-menu-item" onclick="closeAgentGroupQuickSwitcher();openAddGroup()">'
    + '<span aria-hidden="true">+</span> New group</button>';
  html += '</div></div>';
  html += '</div>';
  return html;
}

function _scrollAgentGroupTabs(event) {
  const list = event && event.currentTarget;
  if (!list || typeof list.scrollLeft !== 'number') return;
  const delta = Math.abs(event.deltaY || 0) >= Math.abs(event.deltaX || 0)
    ? (event.deltaY || 0)
    : (event.deltaX || 0);
  if (!delta || list.scrollWidth <= list.clientWidth) return;
  list.scrollLeft += delta;
  if (typeof event.preventDefault === 'function') event.preventDefault();
}

function agentGroupTabKeydown(event, group) {
  if (!event) return;
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    if (typeof onGroupTabClick === 'function') onGroupTabClick(group, event);
    return;
  }
  if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(event.key) < 0) return;
  const groups = _groupNamesSorted();
  if (!groups.length) return;
  let index = groups.indexOf(group);
  if (event.key === 'Home') index = 0;
  else if (event.key === 'End') index = groups.length - 1;
  else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + groups.length) % groups.length;
  event.preventDefault();
  if (typeof setActiveGroup === 'function') setActiveGroup(groups[index]);
  const focusActive = function() {
    const tab = document.querySelector && document.querySelector('.agent-group-tab.active');
    if (tab && typeof tab.focus === 'function') tab.focus();
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(focusActive);
  else focusActive();
}

function openAgentGroupTabActions(event, group) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  if (typeof onGroupTabContextMenu === 'function') onGroupTabContextMenu(event, group);
}

function _agentGroupQuickSwitcher() {
  return document.querySelector && document.querySelector('.agent-group-quick-switcher');
}

function closeAgentGroupQuickSwitcher(restoreFocus) {
  const popover = _agentGroupQuickSwitcher();
  if (!popover) return;
  popover.hidden = true;
  const trigger = popover.parentNode && popover.parentNode.querySelector
    ? popover.parentNode.querySelector('.agent-group-compact-trigger')
    : null;
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
  if (restoreFocus && trigger && typeof trigger.focus === 'function') trigger.focus();
}

function toggleAgentGroupQuickSwitcher(event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  const popover = _agentGroupQuickSwitcher();
  if (!popover) return;
  const opening = !!popover.hidden;
  popover.hidden = !opening;
  const trigger = popover.parentNode && popover.parentNode.querySelector
    ? popover.parentNode.querySelector('.agent-group-compact-trigger')
    : null;
  if (trigger) trigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
  if (!opening) return;
  const input = popover.querySelector && popover.querySelector('.agent-group-quick-search');
  if (input) {
    input.value = '';
    filterAgentGroupQuickSwitcher('');
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(function() { input.focus(); });
    else if (typeof input.focus === 'function') input.focus();
  }
}

function filterAgentGroupQuickSwitcher(query) {
  const popover = _agentGroupQuickSwitcher();
  if (!popover || !popover.querySelectorAll) return;
  const wanted = String(query || '').trim().toLowerCase();
  popover.querySelectorAll('[data-group-switch-option]').forEach(function(button) {
    const haystack = String(button.dataset && button.dataset.search || '').toLowerCase();
    button.hidden = !!wanted && haystack.indexOf(wanted) < 0;
  });
}

function agentGroupQuickSwitcherKeydown(event) {
  if (!event) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeAgentGroupQuickSwitcher(true);
    return;
  }
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
  const popover = _agentGroupQuickSwitcher();
  if (!popover || !popover.querySelectorAll) return;
  const options = Array.prototype.slice.call(popover.querySelectorAll('[data-group-switch-option]'))
    .filter(function(button) { return !button.hidden; });
  if (!options.length) return;
  event.preventDefault();
  const target = event.key === 'ArrowUp' ? options[options.length - 1] : options[0];
  if (target && typeof target.focus === 'function') target.focus();
}

function selectAgentGroupFromQuickSwitcher(group) {
  closeAgentGroupQuickSwitcher();
  if (typeof onGroupTabClick === 'function') onGroupTabClick(group);
}
