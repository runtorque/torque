/* WebSocket inbound message router. */

function _handleWsMessage(e) {
    _noteWsInbound();
    const msg = JSON.parse(e.data);
    if (typeof _compactHandleLazyResponse === 'function'
        && _compactHandleLazyResponse(msg)) {
      if (typeof renderActivePanel === 'function') renderActivePanel();
      return;
    }
    if (typeof behaviorOverlayReceiveMessage === 'function'
        && behaviorOverlayReceiveMessage(msg)) {
      return;
    }
    if (typeof inboxReceiveCommandMessage === 'function'
        && inboxReceiveCommandMessage(msg)) {
      return;
    }
    if (!msg.type && msg.agent_class_status) {
      if (typeof agentClassManagerReceiveLaunchResult === 'function') {
        agentClassManagerReceiveLaunchResult(msg);
      }
      return;
    }
    if (msg.type === 'state') {
      _handleFullState(msg);
    } else if (msg.type === 'delta') {
      _handleDelta(msg);
    } else if (msg.type === 'focus_update') {
      _handleClientFocusUpdate(msg);
    } else if (msg.type === 'config') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      if (msg.runtime && typeof loadDaemonStatus === 'function') loadDaemonStatus();
      if (msg.runtime && typeof refreshDaemonStatusIndicator === 'function') {
        refreshDaemonStatusIndicator();
      }
      if (msg.runtime && typeof refreshStatusBar === 'function') {
        refreshStatusBar({ runtime: true });
      }
      if (_pendingModal) {
        _showAddModal(_pendingModal.mode, _pendingModal.group, msg);
        _pendingModal = null;
      }
    } else if (msg.type === 'group_settings') {
      if (msg.providers) _cachedProviders = msg.providers;
      if (msg.roles || msg.templates) _cachedAgentTemplates = _wsRoleList(msg);
      if (msg.runtime) state.runtime = msg.runtime;
      if (msg.runtime && typeof loadDaemonStatus === 'function') loadDaemonStatus();
      if (msg.runtime && typeof refreshDaemonStatusIndicator === 'function') {
        refreshDaemonStatusIndicator();
      }
      if (msg.runtime && typeof refreshStatusBar === 'function') {
        refreshStatusBar({ runtime: true });
      }
      _showGroupSettings(msg.group, msg);
    } else if (msg.type === 'toast') {
      _showToast(msg.message, msg.level);
    } else if (msg.type === 'system_banner') {
      if (typeof _applySystemBanner === 'function') {
        _applySystemBanner(msg.banner);
      }
    } else if (msg.type === 'supervisor_sessions') {
      if (typeof supervisorReceiveSessions === 'function') {
        supervisorReceiveSessions(msg);
      }
    } else if (msg.type === 'supervisor_restart') {
      if (msg.runtime) {
        state.runtime = msg.runtime;
        if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
        if (typeof refreshDaemonStatusIndicator === 'function') {
          refreshDaemonStatusIndicator();
        }
        if (typeof refreshStatusBar === 'function') {
          refreshStatusBar({ runtime: true });
        }
        if (typeof healthSupervisorRuntimeReceive === 'function') {
          healthSupervisorRuntimeReceive(state.runtime && state.runtime.supervisor);
        }
      }
      if (typeof supervisorReceiveRestart === 'function') {
        supervisorReceiveRestart(msg);
      }
    } else if (msg.type === 'system_health_metrics') {
      if (typeof healthReceiveMetrics === 'function') {
        healthReceiveMetrics(msg);
      }
    } else if (msg.type === 'metrics_tick') {
      if (typeof healthMetricsReceiveTick === 'function') {
        healthMetricsReceiveTick(msg);
      }
    } else if (msg.type === 'metrics_history') {
      if (typeof healthMetricsReceiveHistory === 'function') {
        healthMetricsReceiveHistory(msg);
      }
    } else if (msg.type === 'deploy_state') {
      if (typeof statusBarReceiveDeployState === 'function') {
        statusBarReceiveDeployState(msg);
      }
    } else if (msg.type === 'mission_control_summary') {
      if (typeof missionControlReceiveSummary === 'function') {
        missionControlReceiveSummary(msg);
      }
    } else if (msg.type === 'daemon_stop') {
      if (typeof _daemonStopRequestedByUser !== 'undefined'
          && _daemonStopRequestedByUser
          && typeof _showDaemonStoppedBanner === 'function') {
        _showDaemonStoppedBanner();
      }
    } else if (msg.type === 'worktree_history') {
      _showWorktreeHistory(msg);
    } else if (msg.type === 'worktree_pr') {
      _showWorktreePR(msg);
    } else if (msg.type === 'worktree_diff_full') {
      if (typeof diffReceiveFull === 'function') diffReceiveFull(msg);
    } else if (msg.type === 'worktree_check_merge') {
      if (typeof diffReceiveMergeCheck === 'function') diffReceiveMergeCheck(msg);
    } else if (msg.type === 'worktree_merge_progress') {
      if (typeof diffReceiveMergeProgress === 'function') diffReceiveMergeProgress(msg);
    } else if (msg.type === 'worktree_merge') {
      if (typeof diffReceiveMergeResult === 'function') diffReceiveMergeResult(msg);
    } else if (msg.type === 'worktree_rebase') {
      if (typeof diffReceiveRebaseResult === 'function') diffReceiveRebaseResult(msg);
    } else if (msg.type === 'actions') {
      if (typeof _boardCacheDispatchActionList === 'function') {
        _boardCacheDispatchActionList(msg);
      }
      if (typeof _boardBatchActionWaiting !== 'undefined' && _boardBatchActionWaiting) {
        _handleBoardBatchActionList(msg);
      } else if (typeof _schedModalWaiting !== 'undefined' && _schedModalWaiting) {
        _handleScheduleActionList(msg);
      } else if (typeof _taskModalWaiting !== 'undefined' && _taskModalWaiting) {
        _handleTaskActionList(msg);
      } else if (typeof _boardEligibilityActionWaiting !== 'undefined'
          && _boardEligibilityActionWaiting
          && typeof _boardHandleEligibilityActionList === 'function') {
        _boardHandleEligibilityActionList(msg);
      } else if ((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions')) {
        tplEditorReceiveList(msg);
      } else {
        // Ignore unsolicited action lists after reconnect/startup.
      }
    } else if (msg.type === 'roles' || msg.type === 'templates') {
      _cachedAgentTemplates = _wsRoleList(msg);
      if (typeof _boardCacheDispatchTemplateList === 'function') {
        _boardCacheDispatchTemplateList(msg);
      }
      if (typeof _taskTemplateWaiting !== 'undefined' && _taskTemplateWaiting) {
        _handleTaskTemplateList(msg);
      } else if (typeof _boardEligibilityTemplateWaiting !== 'undefined'
          && _boardEligibilityTemplateWaiting
          && typeof _boardHandleEligibilityTemplateList === 'function') {
        _boardHandleEligibilityTemplateList(msg);
      } else if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof agentTemplateReceiveList === 'function') {
        agentTemplateReceiveList(msg);
      } else if (((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions'))
          && typeof renderTemplatesEditor === 'function') {
        renderTemplatesEditor();
      }
    } else if (msg.type === 'template_detail') {
      if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof agentTemplateReceiveDetail === 'function') {
        agentTemplateReceiveDetail(msg);
      }
    } else if (msg.type === 'specializations') {
      state.specializations = Array.isArray(msg.specializations)
        ? msg.specializations
        : [];
      state.specializations_group = msg.group || '';
      if (typeof renderEngineerLaunchSpecializations === 'function') {
        renderEngineerLaunchSpecializations();
      }
      if (typeof renderAddEngineerSpecializations === 'function') {
        renderAddEngineerSpecializations();
      }
      if (typeof renderGsEngineerSpecializations === 'function') {
        renderGsEngineerSpecializations();
      }
      if (typeof renderEditEngineerSpecializations === 'function') {
        renderEditEngineerSpecializations();
      }
      if (typeof agentPanelRenderEngineerSpecializationsEditor === 'function') {
        agentPanelRenderEngineerSpecializationsEditor();
      }
      if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof specializationLibraryReceiveList === 'function') {
        specializationLibraryReceiveList(msg);
      }
    } else if (msg.type === 'specialization_detail') {
      state.specialization_detail = msg.specialization || null;
      if (((typeof _panelAppVisible === 'function' && _panelAppVisible('templates'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'templates'))
          && typeof specializationLibraryReceiveDetail === 'function') {
        specializationLibraryReceiveDetail(msg);
      }
    } else if (msg.type === 'agent_classes') {
      state.agent_classes = Array.isArray(msg.classes) ? msg.classes : [];
      state.agent_class_issues = Array.isArray(msg.issues) ? msg.issues : [];
      if (typeof agentPanelReceiveAgentClasses === 'function') {
        agentPanelReceiveAgentClasses(msg);
      }
      if (typeof agentClassManagerReceiveList === 'function') {
        agentClassManagerReceiveList(msg);
      }
    } else if (msg.type === 'agent_class_preview') {
      state.agent_class_preview = msg.agent_class || null;
      if (typeof agentPanelReceiveAgentClassPreview === 'function') {
        agentPanelReceiveAgentClassPreview(msg);
      }
      if (typeof agentClassManagerReceivePreview === 'function') {
        agentClassManagerReceivePreview(msg);
      }
    } else if (msg.type === 'agent_class_validation') {
      state.agent_class_validation = msg || null;
      state.agent_class_draft_preview = msg.agent_class || null;
      if (typeof agentClassManagerReceiveValidation === 'function') {
        agentClassManagerReceiveValidation(msg);
      }
    } else if (msg.type === 'agent_class_save' || msg.type === 'agent_class_archive' || msg.type === 'agent_class_delete') {
      state.agent_class_authoring_result = msg || null;
      if (Array.isArray(msg.classes)) {
        state.agent_classes = msg.classes;
      }
      if (Array.isArray(msg.registry_issues)) {
        state.agent_class_issues = msg.registry_issues;
      }
      if (msg.agent_class) {
        state.agent_class_preview = msg.agent_class;
      }
      if (typeof agentClassManagerReceiveMutation === 'function') {
        agentClassManagerReceiveMutation(msg);
      }
    } else if (msg.type === 'agent_class_launch') {
      state.agent_class_launch_result = msg || null;
      if (typeof agentClassManagerReceiveLaunchResult === 'function') {
        agentClassManagerReceiveLaunchResult(msg);
      }
    } else if (msg.type === 'agent_class_assignment') {
      state.agent_class_assignment = msg.status || null;
      var classStatus = msg.status || {};
      var classAgentId = classStatus.agent_id || '';
      var classCell = classAgentId && state.agents ? state.agents[classAgentId] : null;
      if (classCell) {
        classCell.agent_class_id = String(classStatus.assigned_class_id || '').trim();
        classCell.agent_class_version = String(classStatus.assigned_class_version || '').trim();
        classCell.agent_class_assigned_at = Number(classStatus.assigned_at || classCell.agent_class_assigned_at || 0) || 0;
        classCell.agent_class_assigned_by = String(classStatus.assigned_by || classCell.agent_class_assigned_by || '').trim();
        classCell.agent_class_status = classStatus;
      }
      if (typeof agentPanelReceiveAgentClassAssignment === 'function') {
        agentPanelReceiveAgentClassAssignment(msg);
      }
    } else if (msg.type === 'agent_class_status') {
      state.agent_class_status = msg.status || null;
      if (typeof agentPanelReceiveAgentClassStatus === 'function') {
        agentPanelReceiveAgentClassStatus(msg);
      }
    } else if (msg.type === 'engineer_specializations') {
      if (typeof agentPanelReceiveEngineerSpecializations === 'function') {
        agentPanelReceiveEngineerSpecializations(msg);
      } else {
        const agents = state.agents || {};
        const cell = agents[msg.engineer_id];
        if (cell) {
          cell.engineer_specializations = msg.specializations || [];
        }
      }
      if (typeof renderEditEngineerSpecializations === 'function') {
        renderEditEngineerSpecializations();
      }
    } else if (msg.type === 'template_rendered') {
      if (typeof _handleRenderedTemplate === 'function') {
        _handleRenderedTemplate(msg);
      }
    } else if (msg.type === 'action_detail') {
      if ((typeof _panelAppVisible === 'function' && _panelAppVisible('actions'))
          || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'actions')) {
        tplEditorReceiveDetail(msg);
      }
    } else if (msg.type === 'prompt_preview') {
      _showPromptPreview(msg);
    } else if (msg.type === 'system_prompt_preview') {
      if (typeof _showSystemPromptPreview === 'function') {
        _showSystemPromptPreview(msg);
      }
    } else if (msg.type === 'dispatch_action_missing') {
      _handleDispatchActionMissing(msg);
    } else if (msg.type === 'external_open') {
      if (msg.url) window.open(msg.url);
    } else if (msg.type === 'external_imported') {
      if (typeof initiativesHandleBoardTaskCreated === 'function') initiativesHandleBoardTaskCreated(msg);
      _showToast('Imported external ticket', 'info');
    } else if (msg.type === 'board_task_added') {
      if (typeof initiativesHandleBoardTaskCreated === 'function') initiativesHandleBoardTaskCreated(msg);
    } else if (msg.type === 'external_linked') {
      _showToast('External issue linked', 'info');
    } else if (msg.type === 'external_unlinked') {
      _showToast('External issue unlinked', 'info');
    } else if (msg.type === 'external_status_pushed') {
      _showToast('External status pushed', 'info');
    } else if (msg.type === 'external_comment_posted') {
      _showToast('External comment posted', 'info');
    } else if (msg.type === 'board_sync_preflight') {
      if (typeof _handleBoardSyncPreflight === 'function') _handleBoardSyncPreflight(msg);
    } else if (msg.type === 'board_sync_list_projects') {
      if (typeof _handleBoardSyncProjects === 'function') _handleBoardSyncProjects(msg);
    } else if (msg.type === 'board_sync_task') {
      if (typeof _handleBoardSyncTaskResponse === 'function') _handleBoardSyncTaskResponse(msg);
    } else if (msg.type === 'board_pull_preview') {
      if (typeof _handleBoardPullPreview === 'function') _handleBoardPullPreview(msg);
    } else if (msg.type === 'board_pull_apply') {
      if (typeof _handleBoardPullApply === 'function') _handleBoardPullApply(msg);
    } else if (msg.type === 'pipelines') {
      if (typeof tplReceivePipelines !== 'undefined') tplReceivePipelines(msg);
    } else if (msg.type === 'relay_test_result') {
      if (typeof handleRelayTestResult === 'function') handleRelayTestResult(msg);
    } else if (msg.type === 'relay_device_link') {
      if (typeof handleRelayDeviceLink === 'function') handleRelayDeviceLink(msg);
    } else if (msg.type === 'daemon_credential') {
      if (typeof handleRelayDaemonCredential === 'function') handleRelayDaemonCredential(msg);
    } else if (msg.type === 'global_settings') {
      _showGlobalSettingsModal(msg);
    } else if (msg.type === 'ai_settings') {
      if (typeof aiSettingsReceive === 'function') aiSettingsReceive(msg);
    } else if (msg.type === 'ai_settings_requires_confirmation') {
      if (typeof aiSettingsRequiresConfirmation === 'function') aiSettingsRequiresConfirmation(msg);
    } else if (msg.type === 'ai_index_job') {
      if (typeof aiIndexJobReceive === 'function') aiIndexJobReceive(msg);
    } else if (msg.type === 'memory_entries') {
      if (typeof handleContextEntries === 'function') handleContextEntries(msg);
    } else if (msg.type === 'memory_entry') {
      if (typeof handleContextEntry === 'function') handleContextEntry(msg);
    } else if (msg.type === 'initiative_list') {
      if (typeof initiativesReceiveList === 'function') initiativesReceiveList(msg);
    } else if (msg.type === 'initiative') {
      if (typeof initiativesReceiveDetail === 'function') initiativesReceiveDetail(msg);
    } else if (msg.type === 'initiative_created' || msg.type === 'initiative_updated' || msg.type === 'initiative_archived') {
      if (typeof initiativesReceiveMutation === 'function') initiativesReceiveMutation(msg);
    } else if (msg.type === 'initiative_task_linked' || msg.type === 'initiative_task_unlinked' || msg.type === 'initiative_decision_linked' || msg.type === 'initiative_decision_unlinked') {
      if (typeof initiativesReceiveLinkMutation === 'function') initiativesReceiveLinkMutation(msg);
    } else if (msg.type === 'area_list' || msg.type === 'planning_area_list') {
      if (typeof areasReceiveList === 'function') areasReceiveList(msg);
    } else if (msg.type === 'area' || msg.type === 'planning_area') {
      if (typeof areasReceiveDetail === 'function') areasReceiveDetail(msg);
    } else if (msg.type === 'area_created' || msg.type === 'area_updated' || msg.type === 'area_archived' || msg.type === 'planning_area_created' || msg.type === 'planning_area_updated' || msg.type === 'planning_area_archived') {
      if (typeof areasReceiveMutation === 'function') areasReceiveMutation(msg);
    } else if (msg.type === 'area_linked' || msg.type === 'area_unlinked' || msg.type === 'planning_area_linked' || msg.type === 'planning_area_unlinked') {
      if (typeof areasReceiveLinkMutation === 'function') areasReceiveLinkMutation(msg);
    } else if (msg.type === 'area_note_created' || msg.type === 'area_note_updated' || msg.type === 'area_note_archived' || msg.type === 'planning_area_note_created' || msg.type === 'planning_area_note_updated' || msg.type === 'planning_area_note_archived') {
      if (typeof areasReceiveNoteMutation === 'function') areasReceiveNoteMutation(msg);
    } else if (msg.type === 'scratchpad_note_list') {
      if (typeof thinkingReceiveScratchpadList === 'function') thinkingReceiveScratchpadList(msg);
    } else if (msg.type === 'scratchpad_note_created' || msg.type === 'scratchpad_note_updated' || msg.type === 'scratchpad_note_archived' || msg.type === 'scratchpad_note_deleted') {
      if (typeof thinkingReceiveScratchpadMutation === 'function') thinkingReceiveScratchpadMutation(msg);
    } else if (msg.type === 'idea_brief_list') {
      if (!state.idea_briefs) state.idea_briefs = {};
      (msg.idea_briefs || []).forEach(function(brief) {
        if (brief && brief.id) state.idea_briefs[brief.id] = Object.assign({}, brief);
      });
      if (typeof ideaBriefReceiveList === 'function') ideaBriefReceiveList(msg);
    } else if (msg.type === 'idea_brief'
        || msg.type === 'idea_brief_created'
        || msg.type === 'idea_brief_updated'
        || msg.type === 'idea_brief_refined'
        || msg.type === 'idea_brief_parked'
        || msg.type === 'idea_brief_archived'
        || msg.type === 'idea_brief_proposed') {
      if (!state.idea_briefs) state.idea_briefs = {};
      var ideaBrief = msg.idea_brief || (msg.type === 'idea_brief' ? msg : null);
      if (ideaBrief && ideaBrief.id) state.idea_briefs[ideaBrief.id] = Object.assign({}, state.idea_briefs[ideaBrief.id] || {}, ideaBrief);
      if (typeof ideaBriefReceiveMutation === 'function') ideaBriefReceiveMutation(msg);
    } else if (msg.type === 'error') {
      if (typeof healthMetricsReceiveHistory === 'function'
          && typeof healthMetricsState !== 'undefined'
          && healthMetricsState
          && healthMetricsState.historyLoading
          && !((typeof healthState !== 'undefined' && healthState && healthState.loading))
          && ((typeof _panelAppVisible === 'function' && _panelAppVisible('health'))
            || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'health'))) {
        healthMetricsReceiveHistory(msg);
        return;
      }
      if (typeof healthReceiveMetrics === 'function'
          && typeof healthState !== 'undefined'
          && healthState
          && healthState.loading
          && ((typeof _panelAppVisible === 'function' && _panelAppVisible('health'))
            || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'health'))) {
        healthReceiveMetrics(msg);
        return;
      }
      if (typeof missionControlHandleError === 'function' && missionControlHandleError(msg)) return;
      if (typeof areasHandleError === 'function' && areasHandleError(msg)) return;
      if (typeof initiativesHandleError === 'function' && initiativesHandleError(msg)) return;
      if (typeof ideaBriefHandleError === 'function' && ideaBriefHandleError(msg)) return;
      var systemPromptErrorHandled = false;
      if (typeof _showSystemPromptPreviewError === 'function') {
        systemPromptErrorHandled = _showSystemPromptPreviewError(msg);
      }
      var specializationEditorErrorHandled = false;
      if (!systemPromptErrorHandled
          && typeof agentPanelHandleEngineerSpecializationsError === 'function') {
        specializationEditorErrorHandled = agentPanelHandleEngineerSpecializationsError(msg);
      }
      var agentClassErrorHandled = false;
      if (!systemPromptErrorHandled
          && !specializationEditorErrorHandled
          && typeof agentPanelHandleAgentClassError === 'function') {
        agentClassErrorHandled = agentPanelHandleAgentClassError(msg);
      }
      if (!systemPromptErrorHandled
          && !specializationEditorErrorHandled
          && !agentClassErrorHandled
          && typeof agentClassManagerHandleError === 'function') {
        agentClassErrorHandled = agentClassManagerHandleError(msg);
      }
      if (!systemPromptErrorHandled && !specializationEditorErrorHandled && !agentClassErrorHandled) {
        if (typeof aiSettingsHandleError === 'function' && aiSettingsHandleError(msg)) return;
        if (typeof thinkingHandleError === 'function' && thinkingHandleError(msg)) return;
        if (typeof handleContextError === 'function') handleContextError(msg);
        else if (typeof _showToast === 'function' && msg.message) _showToast(msg.message, 'error');
      }
    } else if (msg.type === 'events_page') {
      if (typeof handleEventsPage === 'function') handleEventsPage(msg);
    } else if (msg.type === 'cell_events') {
      if (typeof agentPanelReceiveCellEvents === 'function') agentPanelReceiveCellEvents(msg);
    } else if (msg.type === 'mcp_calls') {
      if (typeof agentPanelReceiveMcpCalls === 'function') agentPanelReceiveMcpCalls(msg);
    } else if (msg.type === 'architect_peers') {
      if (typeof agentPanelReceiveArchitectPeerList === 'function') agentPanelReceiveArchitectPeerList(msg);
    } else if (msg.type === 'agent_message_history') {
      if (!state.agent_message_history) state.agent_message_history = {};
      state.agent_message_history[msg.agent_id] = Array.isArray(msg.history)
        ? msg.history
        : [];
    } else if (msg.type === 'architect_journal_entries') {
      if (typeof agentPanelReceiveArchitectJournal === 'function') agentPanelReceiveArchitectJournal(msg);
    } else if (msg.type === 'agent_history_list') {
      if (typeof agentHistoryReceiveList === 'function') agentHistoryReceiveList(msg);
    } else if (msg.type === 'agent_history_detail') {
      if (typeof agentHistoryReceiveDetail === 'function') agentHistoryReceiveDetail(msg);
      if (typeof taskHistoryReceiveDetail === 'function') taskHistoryReceiveDetail(msg);
    } else if (msg.type === 'action') {
      handleAction(msg);
    } else if (msg.type === 'engineer_session_map') {
      _handleEngineerSessionMapMessage(msg);
    }
}

function _handleEngineerSessionMapMessage(msg) {
  if (!state.engineer_session_maps) state.engineer_session_maps = {};
  var group = (msg && msg.group) || '';
  if (!group) return;
  var engineerId = String((msg && msg.engineer_id) || '').trim();
  var key = engineerId ? (group + '::' + engineerId) : group;
  state.engineer_session_maps[key] = (msg && msg.session_map) || {};
  if (typeof _engineerReceiveSessionMap === 'function') {
    _engineerReceiveSessionMap(msg);
    return;
  }
  if (((typeof _panelAppVisible === 'function' && _panelAppVisible('engineer'))
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'engineer'))
      && typeof renderAgentPanel === 'function') {
    var currentGroup = (typeof _currentGroup === 'function') ? _currentGroup() : '';
    if (!currentGroup || currentGroup === group) renderAgentPanel();
  }
}

function handleAction(msg) {
  if (msg.action === 'close_cell') {
    if (msg.cell_id) removeAgent(msg.cell_id);
  } else if (msg.action === 'add_agent') {
    if (msg.group) quickAddAgent(msg.group);
  } else if (msg.action === 'add_engineer') {
    if (typeof openAddEngineerModal === 'function') openAddEngineerModal();
  } else if (msg.action === 'add_architect') {
    if (typeof openAddArchitectModal === 'function') openAddArchitectModal(msg.group || '');
  } else if (msg.action === 'add_terminal') {
    // Manual terminal creation is no longer exposed in the operator UI. Ignore
    // stale server-side action shims rather than spawning a terminal.
    return false;
  }
}
