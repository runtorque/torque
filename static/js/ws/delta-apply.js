/* Delta state application. */

function _applyDelta(ops) {
  for (const op of ops) {
    const registered = _deltaOperationSpec(op.op);
    if (registered && typeof registered.apply === 'function') {
      registered.apply(op);
      continue;
    }
    switch (op.op) {
      case 'agent_upsert': {
        const id = op.id;
        const previousAgent = state.agents[id] ? Object.assign({}, state.agents[id]) : null;
        if (state.agents[id]) {
          Object.assign(state.agents[id], op);
        } else {
          state.agents[id] = Object.assign({}, op);
        }
        // Clean the 'op' key from the agent data
        delete state.agents[id].op;
        if (!Object.prototype.hasOwnProperty.call(op, 'agent_class_status')) {
          const carriesAgentClassFields = Object.prototype.hasOwnProperty.call(op, 'agent_class_id')
            || Object.prototype.hasOwnProperty.call(op, 'agent_class_version')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_id')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_version')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_snapshot')
            || Object.prototype.hasOwnProperty.call(op, 'effective_agent_class_applied_at');
          if (carriesAgentClassFields) delete state.agents[id].agent_class_status;
        }
        if (Object.prototype.hasOwnProperty.call(op, 'mcp_messages')
            && typeof _agentPanelInvalidateArchitectMessageCache === 'function') {
          _agentPanelInvalidateArchitectMessageCache(id);
          if (previousAgent && previousAgent.id && previousAgent.id !== id) {
            _agentPanelInvalidateArchitectMessageCache(previousAgent.id);
          }
        }
        if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function') {
          var prevKind = String((previousAgent && previousAgent.kind) || '');
          var nextKind = String((state.agents[id] && state.agents[id].kind) || '');
          var groupChanged = previousAgent
            && String(previousAgent.group || '') !== String((state.agents[id] && state.agents[id].group) || '');
          if (prevKind === 'architect' || nextKind === 'architect' || groupChanged) {
            _agentPanelInvalidateArchitectPeerListCache();
          }
        }
        break;
      }
      case 'agent_remove':
        var removedAgent = state.agents ? state.agents[op.id] : null;
        delete state.agents[op.id];
        if (state.agent_digest_settings) delete state.agent_digest_settings[op.id];
        if (state.digest_buffer_stats) delete state.digest_buffer_stats[op.id];
        if (state.digest_sent_events) delete state.digest_sent_events[op.id];
        if (state.agent_message_history) delete state.agent_message_history[op.id];
        if (state.direct_messages_by_agent) delete state.direct_messages_by_agent[op.id];
        if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function'
            && (!removedAgent || String(removedAgent.kind || '') === 'architect')) {
          _agentPanelInvalidateArchitectPeerListCache();
        }
        // Selection/focus globals are browser-local — the server doesn't know
        // about them. Selections can be cleared immediately; focusedItemId is
        // left intact until render() can use previous grid-row metadata to pick
        // the next logical focus target.
        if (typeof selectedAgentId !== 'undefined' && selectedAgentId === op.id) {
          selectedAgentId = null;
        }
        if (typeof selectedTerminalId !== 'undefined' && selectedTerminalId === op.id) {
          selectedTerminalId = null;
        }
        // Keep focusedItemId until render() rebuilds the grid navigation model.
        // The renderer has the previous row metadata and can fall forward to the
        // next logical cell instead of dropping keyboard focus on every remove.
        if (typeof _clearAgentDoneFlourish === 'function') {
          _clearAgentDoneFlourish(op.id);
        }
        break;

      case 'peer_message_upsert': {
        var peerMessage = Object.assign({}, op.message || op.entry || {});
        delete peerMessage.op;
        var peerAgentIds = String(op.agent_id || '').trim()
          ? [String(op.agent_id || '').trim()]
          : _peerMessageDeltaAgentIds(op);
        if (!peerAgentIds.length && peerMessage.peer_id) {
          peerAgentIds.push(String(peerMessage.peer_id || ''));
        }
        if (!state.agents) state.agents = {};
        for (var pmi = 0; pmi < peerAgentIds.length; pmi++) {
          var peerAgentId = String(peerAgentIds[pmi] || '');
          var peerAgent = state.agents[peerAgentId];
          if (!peerAgent) continue;
          if (!Array.isArray(peerAgent.mcp_messages)) peerAgent.mcp_messages = [];
          var peerMessageId = String(peerMessage.id || op.id || '');
          var replacedPeerMessage = false;
          if (peerMessageId) {
            for (var pmj = 0; pmj < peerAgent.mcp_messages.length; pmj++) {
              if (String((peerAgent.mcp_messages[pmj] || {}).id || '') === peerMessageId) {
                peerAgent.mcp_messages[pmj] = Object.assign({}, peerAgent.mcp_messages[pmj], peerMessage);
                replacedPeerMessage = true;
                break;
              }
            }
          }
          if (!replacedPeerMessage) peerAgent.mcp_messages.unshift(Object.assign({}, peerMessage));
          if (peerAgent.mcp_messages.length > 50) peerAgent.mcp_messages.length = 50;
          if (typeof _agentPanelInvalidateArchitectMessageCache === 'function') {
            _agentPanelInvalidateArchitectMessageCache(peerAgentId);
          }
        }
        break;
      }

      case 'direct_message_upsert':
      case 'direct_message_read': {
        var directMessage = Object.assign({}, op.message || op.entry || {});
        delete directMessage.op;
        var directAgentIds = String(op.agent_id || '').trim()
          ? [String(op.agent_id || '').trim()]
          : _peerMessageDeltaAgentIds(op);
        if (!state.direct_messages_by_agent) state.direct_messages_by_agent = {};
        for (var dmi = 0; dmi < directAgentIds.length; dmi++) {
          var directAgentId = String(directAgentIds[dmi] || '').trim();
          if (!directAgentId) continue;
          if (!Array.isArray(state.direct_messages_by_agent[directAgentId])) {
            state.direct_messages_by_agent[directAgentId] = [];
          }
          var directMessageId = _directMessageDeltaMessageId(op, directMessage);
          if (directMessageId) {
            if (!directMessage.id) directMessage.id = directMessageId;
            if (!directMessage.message_id) directMessage.message_id = directMessageId;
          }
          var replacedDirectMessage = false;
          if (directMessageId) {
            for (var dmj = 0; dmj < state.direct_messages_by_agent[directAgentId].length; dmj++) {
              var existingDirectId = _directMessageDeltaMessageId(
                {},
                state.direct_messages_by_agent[directAgentId][dmj] || {}
              );
              if (existingDirectId === directMessageId) {
                state.direct_messages_by_agent[directAgentId][dmj] = Object.assign(
                  {},
                  state.direct_messages_by_agent[directAgentId][dmj],
                  directMessage,
                  op.op === 'direct_message_read' ? { read_at: op.read_at || directMessage.read_at || 0 } : {}
                );
                replacedDirectMessage = true;
                break;
              }
            }
          }
          if (!replacedDirectMessage && directMessageId) {
            state.direct_messages_by_agent[directAgentId].push(Object.assign({}, directMessage));
          }
          state.direct_messages_by_agent[directAgentId].sort(function(a, b) {
            var at = _deltaTimestampValue(a && (a.created_at || a.timestamp || a.sent_at));
            var bt = _deltaTimestampValue(b && (b.created_at || b.timestamp || b.sent_at));
            if (at !== bt) return at - bt;
            return _directMessageDeltaMessageId({}, a || {}).localeCompare(
              _directMessageDeltaMessageId({}, b || {})
            );
          });
          var directLimit = Number(op.limit || 100);
          if (!Number.isFinite(directLimit) || directLimit < 1) directLimit = 100;
          if (state.direct_messages_by_agent[directAgentId].length > directLimit) {
            state.direct_messages_by_agent[directAgentId] =
              state.direct_messages_by_agent[directAgentId].slice(-directLimit);
          }
        }
        break;
      }

      case 'agent_message_loop_upsert': {
        if (!state.agent_message_loops || typeof state.agent_message_loops !== 'object') {
          state.agent_message_loops = {};
        }
        var loop = Object.assign({}, op.loop || {});
        var loopId = String(loop.id || op.id || '').trim();
        if (loopId) {
          if (!loop.id) loop.id = loopId;
          state.agent_message_loops[loopId] = loop;
        }
        break;
      }

      case 'agent_peer_thread_upsert': {
        if (!state.agent_peer_threads || typeof state.agent_peer_threads !== 'object') {
          state.agent_peer_threads = {};
        }
        var chatThread = Object.assign({}, op.thread || {});
        var chatThreadId = String(op.thread_id || chatThread.thread_id || '').trim();
        if (chatThreadId && op.thread) {
          if (!chatThread.thread_id) chatThread.thread_id = chatThreadId;
          state.agent_peer_threads[chatThreadId] = chatThread;
          state.agent_peer_threads = _sortAgentPeerThreadMap(state.agent_peer_threads);
        }
        break;
      }

      case 'agent_peer_thread_remove': {
        if (state.agent_peer_threads && op.thread_id) {
          delete state.agent_peer_threads[String(op.thread_id || '')];
          state.agent_peer_threads = _sortAgentPeerThreadMap(state.agent_peer_threads);
        }
        break;
      }

      case 'group_update':
        state.groups[op.name] = op.agents;
        break;
      case 'group_remove':
        delete state.groups[op.name];
        delete state.group_settings[op.name];
        if (state.engineer_buffer_stats) delete state.engineer_buffer_stats[op.name];
        if (state.engineer_sent_events) delete state.engineer_sent_events[op.name];
        if (state.engineer_worklog) delete state.engineer_worklog[op.name];
        if (state.engineer_streams) delete state.engineer_streams[op.name];
        if (state.engineer_session_maps) {
          Object.keys(state.engineer_session_maps).forEach(function(key) {
            if (key === op.name || key.indexOf(op.name + '::') === 0) {
              delete state.engineer_session_maps[key];
            }
          });
        }
        break;
      case 'group_rename': {
        if (state.groups[op.old_name]) {
          state.groups[op.new_name] = state.groups[op.old_name];
          delete state.groups[op.old_name];
        }
        if (state.group_settings && state.group_settings[op.old_name]) {
          state.group_settings[op.new_name] = state.group_settings[op.old_name];
          delete state.group_settings[op.old_name];
        }
        if (state.engineer_buffer_stats && state.engineer_buffer_stats[op.old_name]) {
          state.engineer_buffer_stats[op.new_name] = state.engineer_buffer_stats[op.old_name];
          delete state.engineer_buffer_stats[op.old_name];
        }
        if (state.engineer_sent_events && state.engineer_sent_events[op.old_name]) {
          state.engineer_sent_events[op.new_name] = state.engineer_sent_events[op.old_name];
          delete state.engineer_sent_events[op.old_name];
        }
        if (state.engineer_worklog && state.engineer_worklog[op.old_name]) {
          state.engineer_worklog[op.new_name] = state.engineer_worklog[op.old_name];
          delete state.engineer_worklog[op.old_name];
        }
        if (state.engineer_streams && state.engineer_streams[op.old_name]) {
          state.engineer_streams[op.new_name] = state.engineer_streams[op.old_name];
          delete state.engineer_streams[op.old_name];
        }
        if (state.engineer_session_maps) {
          Object.keys(state.engineer_session_maps).forEach(function(key) {
            if (key === op.old_name) {
              state.engineer_session_maps[op.new_name] = state.engineer_session_maps[key];
              delete state.engineer_session_maps[key];
            } else if (key.indexOf(op.old_name + '::') === 0) {
              var nextKey = op.new_name + key.slice(String(op.old_name).length);
              state.engineer_session_maps[nextKey] = state.engineer_session_maps[key];
              delete state.engineer_session_maps[key];
            }
          });
        }
        break;
      }
      case 'groups_reorder': {
        const newGroups = {};
        for (const name of op.groups) {
          newGroups[name] = state.groups[name] || [];
        }
        state.groups = newGroups;
        break;
      }
      case 'group_settings_update': {
        const name = op.name;
        if (!state.group_settings) state.group_settings = {};
        if (!state.group_settings[name]) state.group_settings[name] = {};
        Object.assign(state.group_settings[name], op);
        delete state.group_settings[name].op;
        delete state.group_settings[name].name;
        break;
      }

      case 'task_upsert': {
        const id = op.id;
        if (!state.board_tasks) state.board_tasks = {};
        const previousTask = state.board_tasks[id]
          ? Object.assign({}, state.board_tasks[id])
          : null;
        if (state.board_tasks[id]) {
          Object.assign(state.board_tasks[id], op);
        } else {
          state.board_tasks[id] = Object.assign({}, op);
        }
        delete state.board_tasks[id].op;
        // Compact deltas refresh summaries but not heavy fields
        // (messages_thread, artifacts, ...). Drop the task from the
        // fully-loaded registry so the next heavy-field consumer
        // re-fetches task_detail instead of reading stale hydration.
        if (typeof _compactTasksFullyLoaded !== 'undefined'
            && _compactTasksFullyLoaded[id]) {
          delete _compactTasksFullyLoaded[id];
        }
        if (typeof _invalidateTaskLookupIndex === 'function') _invalidateTaskLookupIndex();
        _maybeTriggerAgentDoneFlourish(previousTask, state.board_tasks[id]);
        break;
      }
      case 'task_remove':
        if (state.board_tasks) delete state.board_tasks[op.id];
        if (typeof _invalidateTaskLookupIndex === 'function') _invalidateTaskLookupIndex();
        break;

      case 'lanes_update':
        state.board_lanes = op.lanes;
        break;

      case 'global_settings_update': {
        const gs = Object.assign({}, op);
        delete gs.op;
        delete gs.changed_keys;
        state.global_settings = gs;
        if (typeof invalidateEffectiveKeybindings === 'function') {
          invalidateEffectiveKeybindings();
        }
        if (typeof _syncKeybindingSettingsFromGlobal === 'function') {
          _syncKeybindingSettingsFromGlobal(gs);
        }
        if (typeof _syncStatusBarSettingsFromGlobal === 'function') {
          _syncStatusBarSettingsFromGlobal(gs);
        }
        if (typeof _applyEmbeddedTerminalScrollbackFromSettings === 'function') {
          _applyEmbeddedTerminalScrollbackFromSettings();
        }
        break;
      }

      case 'ai_settings_update':
        if (typeof aiSettingsApplyDelta === 'function') aiSettingsApplyDelta(op);
        break;

      case 'ai_index_status_update':
        if (typeof aiIndexStatusApplyDelta === 'function') aiIndexStatusApplyDelta(op);
        break;

      case 'ai_summary_status_update':
        if (typeof aiSummaryStatusApplyDelta === 'function') aiSummaryStatusApplyDelta(op);
        break;

      case 'event_append': {
        if (!state.panel_events) state.panel_events = [];
        var evt = Object.assign({}, op);
        delete evt.op;
        // Replace existing event by ID (for replace_last updates)
        var replaced = false;
        if (evt.id) {
          for (var ei = state.panel_events.length - 1; ei >= 0; ei--) {
            if (state.panel_events[ei].id === evt.id) {
              state.panel_events[ei] = evt;
              replaced = true;
              break;
            }
          }
        }
        if (!replaced) state.panel_events.push(evt);
        if (state.panel_events.length > 500)
          state.panel_events = state.panel_events.slice(-500);
        break;
      }

      case 'mcp_call_append': {
        var call = op.call || {};
        if (typeof agentPanelReceiveMcpCallAppend === 'function') {
          agentPanelReceiveMcpCallAppend(call);
        } else {
          if (String(call.hook_event_name || '') !== 'PostToolUse') break;
          if (!state.mcp_calls) state.mcp_calls = {};
          var callCellId = String(call.cell_id || '');
          if (callCellId) {
            if (!state.mcp_calls[callCellId]) state.mcp_calls[callCellId] = [];
            state.mcp_calls[callCellId].unshift(call);
            if (state.mcp_calls[callCellId].length > 500) {
              state.mcp_calls[callCellId].length = 500;
            }
          }
        }
        break;
      }

      case 'schedule_upsert': {
        if (!state.schedules) state.schedules = {};
        const sid = op.id;
        if (state.schedules[sid]) {
          Object.assign(state.schedules[sid], op);
        } else {
          state.schedules[sid] = Object.assign({}, op);
        }
        delete state.schedules[sid].op;
        break;
      }
      case 'schedule_remove':
        if (state.schedules) delete state.schedules[op.id];
        break;

      case 'ui_update':
        var prevStandaloneVisibleApps = (op.key === 'standalone_panel_layout'
          && typeof _standaloneVisiblePanelApps === 'function'
          && typeof _standalonePanelsEnabled === 'function'
          && _standalonePanelsEnabled())
          ? _standaloneVisiblePanelApps().slice()
          : [];
        if (op.key === 'selected_agent_id'
            && _clientScopedFocusOwnsSelectedAgent()) {
          // Browser terminal focus is client-local once an embedded terminal
          // has sent a client-scoped focus_update.  Do not let a global
          // persisted selected_agent_id update from another/background launch
          // split the UI by moving the agent/focus panels while this client
          // keeps its existing terminal/DM target.
          state.selected_agent_id = selectedAgentId || '';
          break;
        }
        state[op.key] = op.value;
        if (op.key === 'selected_agent_id') {
          _applySelectedAgentFromServer(op.value || '');
        }
        if (op.key === 'standalone_panel_layout') {
          // A detached panel window is a separate full webview instance that
          // shows only its own panel. It must keep `state.standalone_panel_layout`
          // accurate (already patched above), but must NEVER re-apply the main
          // window's layout to its own DOM — doing so parks/hides the detached
          // root (black window) and repoints `_activePanelApp` at the hidden
          // main-window surface.
          if (typeof _detachedWindowActive === 'function' && _detachedWindowActive()) {
            // no-op: state stays in sync, DOM stays pinned to the detached panel
          } else if (typeof _standalonePanelSetLayoutFromState === 'function') {
            _standalonePanelSetLayoutFromState(op.value || {}, { fromServer: true });
            if (typeof _syncVisibleStandalonePanelApps === 'function') {
              _syncVisibleStandalonePanelApps(prevStandaloneVisibleApps);
            }
          }
        }
        if (op.key === 'active_group') {
          if (typeof _lastPersistedActiveGroup !== 'undefined') {
            _lastPersistedActiveGroup = String(op.value || '');
          }
          if (typeof _writeStoredActiveGroup === 'function') {
            _writeStoredActiveGroup(op.value || '');
          }
          if (typeof _pendingActiveGroup !== 'undefined'
              && _pendingActiveGroup === String(op.value || '')) {
            _pendingActiveGroup = '';
          }
        }
        if (op.key === 'workspace_sidebar_width'
            && typeof _applyWorkspaceSidebarWidth === 'function') {
          _applyWorkspaceSidebarWidth(op.value || 0);
        }
        if (op.key === 'context_panel_split_ratio'
            && typeof _contextApplyPersistedSplit === 'function') {
          _contextApplyPersistedSplit();
        }
        if (op.key === 'supervisor_panel_state'
            && typeof supervisorApplyPersistedUiState === 'function') {
          supervisorApplyPersistedUiState(op.value || {});
        }
        if (op.key === 'board_filters_by_group'
            && typeof _boardFiltersByGroup !== 'undefined') {
          _boardFiltersByGroup = null;
          if (typeof _boardFilterStateGroup !== 'undefined') {
            _boardFilterStateGroup = '';
          }
        }
        if (op.key === 'board_selected_lanes_by_group'
            && typeof _boardSelectedLanesByGroup !== 'undefined') {
          _boardSelectedLanesByGroup = null;
          if (typeof _boardSelectedLaneStateGroup !== 'undefined') {
            _boardSelectedLaneStateGroup = '';
          }
        }
        if (op.key === 'board_hidden_wide_lanes_by_group'
            && typeof _boardHiddenWideLanesByGroup !== 'undefined') {
          _boardHiddenWideLanesByGroup = null;
          if (typeof _boardLaneRenderCache !== 'undefined') {
            _boardLaneRenderCache = {};
          }
        }
        if (op.key === 'board_saved_views_by_group'
            && typeof _boardSavedViewsByGroup !== 'undefined') {
          _boardSavedViewsByGroup = null;
        }
        if (op.key === 'board_lane_sorts_by_group'
            && typeof _boardLaneSortsByGroup !== 'undefined') {
          _boardLaneSortsByGroup = null;
        }
        if (op.key === 'board_card_density_by_group'
            && typeof _boardCardDensityByGroup !== 'undefined') {
          _boardCardDensityByGroup = null;
        }
        break;

      case 'journal_append': {
        if (!state.engineer_journal) state.engineer_journal = {};
        var authorId = String((op && op.author_cell_id) || '');
        if (authorId) {
          if (!state.engineer_journal[authorId]) state.engineer_journal[authorId] = [];
          var je = Object.assign({}, op);
          delete je.op;
          state.engineer_journal[authorId].unshift(je);
          // Cap at 200 entries per engineer
          if (state.engineer_journal[authorId].length > 200)
            state.engineer_journal[authorId] = state.engineer_journal[authorId].slice(0, 200);
        }
        break;
      }

      case 'journal_delete': {
        var authorDel = String((op && op.author_cell_id) || '');
        if (authorDel && state.engineer_journal && state.engineer_journal[authorDel]) {
          state.engineer_journal[authorDel] = state.engineer_journal[authorDel].filter(
            function(e) { return e.id !== op.id; });
        } else if (state.engineer_journal) {
          Object.keys(state.engineer_journal).forEach(function(key) {
            var bucket = state.engineer_journal[key];
            if (!Array.isArray(bucket)) return;
            state.engineer_journal[key] = bucket.filter(function(e) {
              return e.id !== op.id;
            });
          });
        }
        break;
      }

      case 'architect_journal_append': {
        var archId = op.architect_id || '';
        if (archId) {
          if (!state.architect_journals) state.architect_journals = {};
          if (!state.architect_journals[archId]) state.architect_journals[archId] = [];
          var entry = Object.assign({}, op);
          delete entry.op;
          var bucket = state.architect_journals[archId];
          bucket.unshift(entry);
          if (bucket.length > 500) bucket.length = 500;
          if (typeof _agentPanelArchitectJournalDidPrepend === 'function') {
            _agentPanelArchitectJournalDidPrepend(archId);
          }
          if (typeof _agentPanelInvalidateArchitectJournalCache === 'function') {
            _agentPanelInvalidateArchitectJournalCache(archId);
          }
        }
        break;
      }

      case 'architect_dismissed':
      case 'architect_rehired':
        if (state && state.agents && op.architect_id && state.agents[op.architect_id]) {
          state.agents[op.architect_id].dismissed_at = (
            op.op === 'architect_dismissed' ? (op.dismissed_at || Date.now() / 1000) : 0
          );
        }
        if (typeof _agentPanelInvalidateArchitectPeerListCache === 'function') {
          _agentPanelInvalidateArchitectPeerListCache();
        }
        break;

      case 'engineer_buffer_stats': {
        if (!state.engineer_buffer_stats) state.engineer_buffer_stats = {};
        var bsg = op.group || '';
        if (bsg) {
          state.engineer_buffer_stats[bsg] = {
            buffered_events: op.buffered_events || 0,
            next_push_in: op.next_push_in || 0,
            next_push_at: op.next_push_at || 0,
            queued_events: op.queued_events || [],
            manual_flush_requested: !!op.manual_flush_requested,
          };
        }
        break;
      }

      case 'engineer_sent_events': {
        if (!state.engineer_sent_events) state.engineer_sent_events = {};
        var wsg = op.group || '';
        if (wsg) {
          state.engineer_sent_events[wsg] = op.events || [];
        }
        break;
      }

      case 'digest_buffer_stats': {
        if (!state.digest_buffer_stats) state.digest_buffer_stats = {};
        var dbg = op.agent_id || '';
        if (dbg) {
          state.digest_buffer_stats[dbg] = {
            agent_id: dbg,
            group: op.group || '',
            buffered_events: op.buffered_events || 0,
            next_push_in: op.next_push_in || 0,
            next_push_at: op.next_push_at || 0,
            queued_events: op.queued_events || [],
            manual_flush_requested: !!op.manual_flush_requested,
          };
        }
        break;
      }

      case 'digest_sent_push': {
        if (!state.digest_sent_events) state.digest_sent_events = {};
        var dsg = op.agent_id || '';
        if (dsg) {
          state.digest_sent_events[dsg] = op.events || [];
        }
        break;
      }

      case 'engineer_worklog_append': {
        if (!state.engineer_worklog) state.engineer_worklog = {};
        var wlg = op.group || '';
        if (wlg) {
          if (!state.engineer_worklog[wlg]) state.engineer_worklog[wlg] = [];
          var worklogEntry = Object.assign({}, op.entry || {});
          state.engineer_worklog[wlg].unshift(worklogEntry);
          if (state.engineer_worklog[wlg].length > 200) {
            state.engineer_worklog[wlg] = state.engineer_worklog[wlg].slice(0, 200);
          }
        }
        break;
      }

      case 'engineer_streams':
      case 'engineer_streams_update': {
        if (!state.engineer_streams) state.engineer_streams = {};
        var wstg = op.group || '';
        if (wstg) {
          if (Object.prototype.hasOwnProperty.call(op, 'streams')) {
            state.engineer_streams[wstg] = op.streams;
          } else if (Array.isArray(op.items)) {
            state.engineer_streams[wstg] = { items: op.items };
          } else {
            state.engineer_streams[wstg] = [];
          }
        }
        break;
      }

      case 'engineer_settings_update': {
        if (!state.engineer_settings) state.engineer_settings = {};
        var wg = op.group || '';
        if (wg) {
          var ws = Object.assign({}, op);
          delete ws.op;
          state.engineer_settings[wg] = ws;
        }
        break;
      }

      case 'agent_digest_update': {
        if (!state.agent_digest_settings) state.agent_digest_settings = {};
        var digestAgentId = op.agent_id || '';
        if (digestAgentId) {
          var digestSettings = Object.assign({}, op);
          delete digestSettings.op;
          state.agent_digest_settings[digestAgentId] = digestSettings;
        }
        break;
      }

      case 'initiative_upsert': {
        if (!state.initiatives) state.initiatives = {};
        var initiative = Object.assign({}, op);
        delete initiative.op;
        if (initiative.id) state.initiatives[initiative.id] = Object.assign({}, state.initiatives[initiative.id] || {}, initiative);
        break;
      }

      case 'initiative_link_upsert':
      case 'initiative_link_remove': {
        if (typeof initiativesLoadDetail === 'function' && op.initiative_id && typeof _initiativesSelectedId !== 'undefined' && _initiativesSelectedId === op.initiative_id) {
          initiativesLoadDetail(op.initiative_id, { force: true });
        }
        break;
      }

      case 'area_upsert':
      case 'planning_area_upsert': {
        if (!state.areas) state.areas = {};
        var area = Object.assign({}, op);
        delete area.op;
        if (area.id) state.areas[area.id] = Object.assign({}, state.areas[area.id] || {}, area);
        break;
      }

      case 'area_link_upsert':
      case 'area_link_remove':
      case 'planning_area_link_upsert':
      case 'planning_area_link_remove': {
        if (typeof areasLoadDetail === 'function' && op.area_id && typeof _areasSelectedId !== 'undefined' && _areasSelectedId === op.area_id) {
          areasLoadDetail(op.area_id, { force: true });
        }
        break;
      }

      case 'area_note_upsert':
      case 'planning_area_note_upsert': {
        if (typeof areasLoadDetail === 'function' && op.area_id && typeof _areasSelectedId !== 'undefined' && _areasSelectedId === op.area_id) {
          areasLoadDetail(op.area_id, { force: true });
        }
        break;
      }

      case 'thinking_scratchpad_note_upsert': {
        if (!state.thinking) state.thinking = { scratchpad_notes: {} };
        if (!state.thinking.scratchpad_notes) state.thinking.scratchpad_notes = {};
        var thinkingNote = Object.assign({}, op);
        delete thinkingNote.op;
        if (thinkingNote.id) state.thinking.scratchpad_notes[thinkingNote.id] = Object.assign({}, state.thinking.scratchpad_notes[thinkingNote.id] || {}, thinkingNote);
        if (typeof thinkingReceiveScratchpadDelta === 'function') thinkingReceiveScratchpadDelta(thinkingNote);
        break;
      }

      case 'idea_brief_upsert': {
        if (!state.idea_briefs) state.idea_briefs = {};
        var deltaIdeaBrief = Object.assign({}, op);
        delete deltaIdeaBrief.op;
        if (deltaIdeaBrief.id) {
          state.idea_briefs[deltaIdeaBrief.id] = Object.assign(
            {},
            state.idea_briefs[deltaIdeaBrief.id] || {},
            deltaIdeaBrief
          );
        }
        if (typeof ideaBriefReceiveDelta === 'function') ideaBriefReceiveDelta(deltaIdeaBrief);
        break;
      }

      case 'decision_upsert': {
        if (!state.decisions) state.decisions = {};
        var decisionId = op.id;
        if (decisionId) {
          var previousDecision = state.decisions[decisionId] || null;
          if (typeof _agentPanelInvalidateArchitectDecisionCache === 'function') {
            _agentPanelInvalidateArchitectDecisionCache(op.architect_id || (previousDecision && previousDecision.architect_id) || '');
            if (previousDecision
                && previousDecision.architect_id
                && previousDecision.architect_id !== op.architect_id) {
              _agentPanelInvalidateArchitectDecisionCache(previousDecision.architect_id);
            }
          }
          var decision = Object.assign({}, op);
          delete decision.op;
          state.decisions[decisionId] = decision;
        }
        break;
      }

      case 'decision_remove':
        if (typeof _agentPanelInvalidateArchitectDecisionCache === 'function') {
          var removedDecision = state.decisions ? state.decisions[op.id] : null;
          _agentPanelInvalidateArchitectDecisionCache(op.architect_id || (removedDecision && removedDecision.architect_id) || '');
        }
        if (state.decisions) delete state.decisions[op.id];
        break;

      case 'pending_hire_upsert': {
        if (!state.pending_hires) state.pending_hires = {};
        var pendingHireId = op.id;
        if (pendingHireId) {
          var pendingHire = Object.assign({}, op);
          delete pendingHire.op;
          state.pending_hires[pendingHireId] = pendingHire;
        }
        break;
      }

      case 'pending_hire_resolve':
        if (state.pending_hires) delete state.pending_hires[op.id];
        break;

      case 'behavior_overlay_version_append':
      case 'behavior_overlay_active_update':
      case 'behavior_overlay_proposal_upsert':
      case 'behavior_overlay_proposal_resolve':
        if (typeof behaviorOverlayApplyDelta === 'function') {
          behaviorOverlayApplyDelta(op);
        }
        break;

      case 'focus_update':
        _applyFocusUpdatePayload(op);
        break;
    }
  }
  // Rebuild parent→children index
  _rebuildChildren();
  if (typeof _pruneAgentDoneFlourishes === 'function') {
    _pruneAgentDoneFlourishes(state.agents || {});
  }
}
