
var _agentGridRetainedExecutionArchitectByGroup = (typeof window !== 'undefined' && window._agentGridRetainedExecutionArchitectByGroup)
  ? window._agentGridRetainedExecutionArchitectByGroup
  : {};
if (typeof window !== 'undefined') window._agentGridRetainedExecutionArchitectByGroup = _agentGridRetainedExecutionArchitectByGroup;

function _agentGridSectionKey(section) {
  if (!section) return '';
  if (section.key) return String(section.key || '');
  if (section.type === 'user') return 'user';
  if (section.architect && section.architect.id) return 'architect:' + String(section.architect.id || '');
  return String(section.type || '');
}

function _agentGridArchitectSectionHasExecution(section) {
  return !!(section && section.architect && Array.isArray(section.rows) && section.rows.length > 0);
}

function _agentGridFindArchitectSectionById(sections, architectId) {
  const wanted = String(architectId || '').trim();
  if (!wanted) return null;
  const list = Array.isArray(sections) ? sections : [];
  for (const section of list) {
    if (section && section.architect && String(section.architect.id || '') === wanted) return section;
  }
  return null;
}

function _agentGridFirstExecutionArchitectSection(sections) {
  const list = Array.isArray(sections) ? sections : [];
  for (const section of list) {
    if (_agentGridArchitectSectionHasExecution(section)) return section;
  }
  return null;
}

function _agentGridResolveExecutionArchitect(groupName, architectSections) {
  const group = String(groupName || '').trim();
  const sections = Array.isArray(architectSections) ? architectSections : [];
  const selectedId = String((typeof selectedAgentId !== 'undefined' && selectedAgentId) || '').trim();
  const selectedSection = _agentGridFindArchitectSectionById(sections, selectedId);
  const previousId = group ? String(_agentGridRetainedExecutionArchitectByGroup[group] || '').trim() : '';
  let executionSection = null;
  let retained = false;
  let reason = '';

  if (selectedSection && _agentGridArchitectSectionHasExecution(selectedSection)) {
    executionSection = selectedSection;
    if (group) _agentGridRetainedExecutionArchitectByGroup[group] = String(selectedSection.architect.id || '');
  } else {
    executionSection = _agentGridFindArchitectSectionById(sections, previousId);
    if (!_agentGridArchitectSectionHasExecution(executionSection)) {
      executionSection = _agentGridFirstExecutionArchitectSection(sections);
      if (executionSection && group && !previousId) {
        _agentGridRetainedExecutionArchitectByGroup[group] = String(executionSection.architect.id || '');
      }
    }
    retained = !!(selectedSection && !_agentGridArchitectSectionHasExecution(selectedSection) && executionSection);
    if (retained) reason = 'selected-architect-has-no-engineers';
  }

  return {
    selectedSection,
    executionSection,
    retained,
    reason,
  };
}

function _agentGridIsTorqueSteward(agent) {
  if (!agent) return false;
  const metadata = agent.effective_agent_class_snapshot && agent.effective_agent_class_snapshot.metadata
    ? agent.effective_agent_class_snapshot.metadata : {};
  return (agent.kind || '') === 'architect'
    && (String(agent.agent_class_id || '') === 'torque-steward'
      || String(agent.effective_agent_class_id || '') === 'torque-steward'
      || String(metadata.archetype || '') === 'torque_steward'
      || String(agent.name || '').trim() === 'Torque Steward');
}

function _sortArchitectsWithStewardPinned(architects, indexById) {
  return _sortAgentsByCreation(architects, indexById).sort(function(a, b) {
    const av = _agentGridIsTorqueSteward(a) ? 0 : 1;
    const bv = _agentGridIsTorqueSteward(b) ? 0 : 1;
    if (av !== bv) return av - bv;
    return 0;
  });
}

function _buildHierarchicalAgentSections(agents) {
  const visibleById = {};
  const architects = [];
  const userEngineers = [];
  const engineersByArchitect = {};
  const workersByEngineer = {};
  const looseWorkers = [];
  const list = Array.isArray(agents) ? agents.slice() : [];
  const indexById = {};

  for (let i = 0; i < list.length; i++) {
    const agent = list[i];
    if (!agent) continue;
    visibleById[agent.id] = agent;
    indexById[agent.id] = i;
  }

  for (const agent of list) {
    if (!agent) continue;
    if ((agent.kind || '') === 'architect') {
      architects.push(agent);
      continue;
    }
    if ((agent.kind || '') === 'engineer') {
      const architectId = String(agent.hired_by_architect_id || '').trim();
      const architect = architectId ? visibleById[architectId] : null;
      if (architect && (architect.kind || '') === 'architect') {
        if (!engineersByArchitect[architectId]) engineersByArchitect[architectId] = [];
        engineersByArchitect[architectId].push(agent);
      } else {
        userEngineers.push(agent);
      }
      continue;
    }
    const ownerId = _workerOwnerEngineerId(agent, visibleById);
    if (_isWorkerLikeAgent(agent) && ownerId) {
      if (!workersByEngineer[ownerId]) workersByEngineer[ownerId] = [];
      workersByEngineer[ownerId].push(agent);
      continue;
    }
    if (_isWorkerLikeAgent(agent) && !_agentRawOwnerEngineerId(agent)) {
      looseWorkers.push(agent);
      continue;
    }
    if (_isWorkerLikeAgent(agent)) looseWorkers.push(agent);
  }

  const visibleEngineerIds = {};
  for (const engineer of userEngineers) visibleEngineerIds[engineer.id] = true;
  for (const architectId in engineersByArchitect) {
    for (const engineer of engineersByArchitect[architectId]) visibleEngineerIds[engineer.id] = true;
  }

  function engineerRows(engineers) {
    const rows = [];
    const sortedEngineers = _sortAgentsByCreation(engineers, indexById);
    for (const engineer of sortedEngineers) {
      rows.push({
        engineer,
        workers: _sortAgentsByCreation(workersByEngineer[engineer.id] || [], indexById),
      });
    }
    return rows;
  }

  const sections = [{
    key: 'user',
    type: 'user',
    architect: null,
    looseWorkers: _sortAgentsByCreation(looseWorkers, indexById),
    rows: engineerRows(userEngineers),
  }];

  const sortedArchitects = _sortArchitectsWithStewardPinned(architects, indexById);
  for (const architect of sortedArchitects) {
    sections.push({
      key: 'architect:' + String(architect.id || ''),
      type: 'architect',
      architect,
      looseWorkers: [],
      rows: engineerRows(engineersByArchitect[architect.id] || []),
    });
  }

  const ordered = [];
  for (const section of sections) {
    if (section.architect) ordered.push(section.architect);
    if (section.type === 'user') {
      for (const worker of section.looseWorkers) ordered.push(worker);
    }
    for (const row of section.rows) {
      ordered.push(row.engineer);
      for (const worker of row.workers) ordered.push(worker);
    }
  }

  return {
    sections,
    orderedAgents: ordered,
    visibleAgentById: visibleById,
    visibleEngineerIds,
  };
}

function _buildStratifiedAgentGridModel(agents) {
  const base = _buildHierarchicalAgentSections(agents);
  let userSection = null;
  const architects = [];
  for (const section of base.sections || []) {
    if (!section) continue;
    if (section.type === 'user') userSection = section;
    else if (section.architect) architects.push(section);
  }
  if (!userSection) {
    userSection = {
      key: 'user',
      type: 'user',
      architect: null,
      looseWorkers: [],
      rows: [],
    };
  }

  const ordered = [];
  for (const section of architects) {
    if (section.architect) ordered.push(section.architect);
    for (const row of section.rows || []) {
      if (row && row.engineer) ordered.push(row.engineer);
      for (const worker of (row && row.workers) || []) ordered.push(worker);
    }
  }
  for (const row of userSection.rows || []) {
    if (row && row.engineer) ordered.push(row.engineer);
    for (const worker of (row && row.workers) || []) ordered.push(worker);
  }
  for (const worker of userSection.looseWorkers || []) ordered.push(worker);

  return {
    sections: base.sections,
    architects,
    engineers: userSection.rows || [],
    workers: userSection.looseWorkers || [],
    userSection,
    orderedAgents: ordered,
    visibleAgentById: base.visibleAgentById,
    visibleEngineerIds: base.visibleEngineerIds,
  };
}

function _sortAgentsHierarchically(agents) {
  return _buildStratifiedAgentGridModel(agents).orderedAgents;
}

function _renderArchitectStrip(groupName, model, renderCell, opts) {
  opts = opts || {};
  const architectSections = (model && Array.isArray(model.architects))
    ? model.architects
    : [];
  if (!architectSections.length) return '';
  let html = '<section class="agent-strata agent-strata--architects agent-strata--architect-strip" data-agent-strata="architects">';
  html += '<div class="agent-architect-strip" data-agent-architect-strip data-agent-row-shape="architect-strip-row">';
  for (const section of architectSections) {
    if (section && section.architect) html += renderCell(section.architect);
  }
  html += '</div>';
  html += '</section>';
  return html;
}

function _renderArchitectRetainedNotice(executionInfo) {
  if (!executionInfo || !executionInfo.retained || !executionInfo.executionSection) return '';
  const selected = executionInfo.selectedSection && executionInfo.selectedSection.architect
    ? executionInfo.selectedSection.architect : null;
  const retained = executionInfo.executionSection.architect || null;
  const selectedName = selected ? (selected.name || selected.slug || selected.id || 'selected Architect') : 'selected Architect';
  const retainedName = retained ? (retained.name || retained.slug || retained.id || 'previous Architect') : 'previous Architect';
  return '<div class="agent-execution-retained-note" data-agent-execution-retained="true">'
    + 'Showing ' + esc(retainedName) + ' execution hierarchy while '
    + esc(selectedName) + ' is selected — this Architect has no Engineers yet.'
    + '</div>';
}

function _renderArchitectExecutionStrata(groupName, executionInfo, renderCell, opts) {
  opts = opts || {};
  executionInfo = executionInfo || {};
  const section = executionInfo.executionSection || null;
  const selectedSection = executionInfo.selectedSection || null;
  if (!section || !section.architect) {
    if (selectedSection && selectedSection.architect) {
      return '<section class="agent-strata agent-strata--architect-execution agent-strata--architect-execution-empty"'
        + ' data-agent-strata="architect-execution"'
        + ' data-execution-selected-architect-id="' + esc(selectedSection.architect.id || '') + '">'
        + '<div class="agent-execution-empty" data-agent-execution-empty="true">'
        + esc((selectedSection.architect.name || selectedSection.architect.slug || selectedSection.architect.id || 'Selected Architect'))
        + ' has no Engineers yet. No retained execution hierarchy is available.'
        + '</div></section>';
    }
    return '';
  }
  const rows = Array.isArray(section.rows) ? section.rows : [];
  const sectionKey = _agentGridSectionKey(section);
  const selectedId = selectedSection && selectedSection.architect ? String(selectedSection.architect.id || '') : '';
  const executionId = String(section.architect.id || '');
  let html = '<section class="agent-strata agent-strata--architect-execution"'
    + ' data-agent-strata="architect-execution"'
    + ' data-agent-section="' + esc(sectionKey) + '"'
    + ' data-execution-architect-id="' + esc(executionId) + '"'
    + (selectedId ? ' data-execution-selected-architect-id="' + esc(selectedId) + '"' : '')
    + (executionInfo.retained ? ' data-execution-retained="true"' : '')
    + '>';
  html += _renderArchitectRetainedNotice(executionInfo);
  html += '<div class="agent-execution-heading" data-agent-execution-heading>'
    + '<span class="agent-execution-heading-label">Execution hierarchy</span>'
    + '<span class="agent-execution-heading-owner">' + esc(section.architect.name || section.architect.slug || section.architect.id || 'Architect') + '</span>'
    + '</div>';
  html += '<section class="agent-band agent-band--architect-execution agent-section agent-section-architect"'
    + ' data-agent-section="' + esc(sectionKey) + '">';
  html += '<div class="agent-band-body agent-section-body agent-execution-body"'
    + ' data-agent-section-column="body"'
    + ' data-section-key="' + esc(sectionKey) + '">';
  if (rows.length) {
    for (const row of rows) html += _renderEngineerRow(row, renderCell);
  }
  html += '</div>';
  html += '</section>';
  html += '</section>';
  return html;
}

function _renderOrphanEngineersStrata(groupName, userSection, renderCell, opts) {
  opts = opts || {};
  userSection = userSection || {
    key: 'user',
    type: 'user',
    architect: null,
    looseWorkers: [],
    rows: [],
  };
  const rows = Array.isArray(userSection.rows) ? userSection.rows : [];
  if (!rows.length) return '';
  let html = '<section class="agent-strata agent-strata--engineers" data-agent-strata="engineers">';
  html += '<section class="agent-band agent-band--orphan-engineers agent-section agent-section-user"'
    + ' data-agent-section="user">';
  html += '<div class="agent-band-body agent-band-body--orphan-engineers agent-section-body"'
    + ' data-agent-section-column="body"'
    + ' data-section-key="user">';
  if (rows.length) {
    for (const row of rows) html += _renderEngineerRow(row, renderCell);
  }
  html += '</div>';
  html += '</section>';
  html += '</section>';
  return html;
}

function _renderOrphanWorkersStrata(groupName, userSection, renderCell, opts) {
  opts = Object.assign({}, opts || {}, { groupName });
  userSection = userSection || {
    key: 'user',
    type: 'user',
    architect: null,
    looseWorkers: [],
    rows: [],
  };
  const workers = Array.isArray(userSection.looseWorkers) ? userSection.looseWorkers : [];
  if (!workers.length) return '';
  let html = '<section class="agent-strata agent-strata--workers" data-agent-strata="workers">';
  html += '<section class="agent-band agent-band--orphan-workers agent-section agent-section-workers"'
    + ' data-agent-section="workers">';
  html += '<div class="agent-band-body agent-band-body--orphan-workers agent-section-body"'
    + ' data-agent-section-column="body"'
    + ' data-section-key="workers">';
  html += _renderStandaloneWorkersStrip(userSection, renderCell, opts);
  html += '</div>';
  html += '</section>';
  html += '</section>';
  return html;
}

function _renderStratifiedAgentGrid(groupName, model, renderCell, opts) {
  opts = Object.assign({}, opts || {}, { groupName });
  const userSection = (model && model.userSection) || null;
  let html = '<div class="agent-grid agent-grid-stratified"'
    + ' data-drop-group="' + esc(groupName) + '"'
    + ' data-drop-type="agent">';
  html += _renderArchitectStrip(groupName, model, renderCell, opts);
  const executionInfo = _agentGridResolveExecutionArchitect(groupName, (model && model.architects) || []);
  html += _renderArchitectExecutionStrata(groupName, executionInfo, renderCell, opts);
  html += _renderOrphanEngineersStrata(groupName, userSection, renderCell, opts);
  html += _renderOrphanWorkersStrata(groupName, userSection, renderCell, opts);
  html += '</div>';
  return html;
}

function _renderStandaloneWorkersStrip(section, renderCell, opts) {
  opts = opts || {};
  if (!section || section.type !== 'user') return '';
  const workers = section.looseWorkers || [];
  if (!workers.length) return '';
  let html = '<div class="loose-workers-strip" data-agent-row-shape="standalone-workers-row">';
  for (const worker of workers) html += renderCell(worker);
  html += '</div>';
  return html;
}

function _renderEngineerRow(row, renderCell) {
  if (!row || !row.engineer) return '';
  const workers = row.workers || [];
  const rowClasses = ['engineer-row', 'agent-grid-engineer-row'];
  if (!workers.length) rowClasses.push('engineer-row--empty-workers');
  let html = '<div class="' + esc(rowClasses.join(' ')) + '"'
    + ' data-agent-row-shape="engineer-row"'
    + ' data-worker-count="' + esc(String(workers.length)) + '"'
    + ' data-engineer-id="' + esc(row.engineer.id || '') + '">';
  html += '<div class="engineer-row-anchor">' + renderCell(row.engineer) + '</div>';
  html += '<div class="engineer-row-workers">';
  for (const worker of workers) html += renderCell(worker);
  html += '</div>';
  html += '</div>';
  return html;
}
