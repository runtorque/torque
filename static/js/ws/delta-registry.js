/* Delta operation registry. */

// Delta operations with targeted side effects live in one registry so state
// application and surface invalidation cannot drift into separate switch
// statements. Broader state-shape operations remain in the legacy switch and
// can move here incrementally without changing the wire protocol.
const _deltaOperationRegistry = Object.create(null);

function _registerDeltaOperations(names, spec) {
  const list = Array.isArray(names) ? names : [names];
  for (const rawName of list) {
    const name = String(rawName || '');
    if (!name) throw new Error('delta operation name is required');
    if (_deltaOperationRegistry[name]) {
      throw new Error('delta operation already registered: ' + name);
    }
    _deltaOperationRegistry[name] = Object.assign({}, spec || {});
  }
}

function _deltaOperationSpec(name) {
  return _deltaOperationRegistry[String(name || '')] || null;
}

function _noBroadSurfaceInvalidation() {
  // Explicit no-op: the operation performs a targeted DOM/state patch only.
}

_registerDeltaOperations('context_update', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    const id = _contextDeltaAgentId(op);
    if (!id || !state.agents || !state.agents[id]) return;
    const payload = _contextWindowPayloadFromOp(op);
    const providerUsage = _providerUsagePayloadFromOp(op);
    if (payload === undefined && providerUsage === undefined) return;
    if (payload !== undefined) {
      state.agents[id].context_window = (payload && typeof payload === 'object')
        ? Object.assign({}, payload)
        : {};
    }
    if (providerUsage !== undefined) {
      state.agents[id].provider_usage = (providerUsage && typeof providerUsage === 'object')
        ? Object.assign({}, providerUsage)
        : providerUsage;
    }
  },
});

_registerDeltaOperations('agent_message_history_append', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    if (!state.agent_message_history) state.agent_message_history = {};
    var historyAgentId = String(op.agent_id || '');
    var historyEntry = op.entry || null;
    if (!historyAgentId || !historyEntry) return;
    if (!Array.isArray(state.agent_message_history[historyAgentId])) {
      state.agent_message_history[historyAgentId] = [];
    }
    state.agent_message_history[historyAgentId].unshift(historyEntry);
    var historyLimit = Number(op.limit || 100);
    if (!Number.isFinite(historyLimit) || historyLimit < 1) historyLimit = 100;
    if (state.agent_message_history[historyAgentId].length > historyLimit) {
      state.agent_message_history[historyAgentId].length = historyLimit;
    }
  },
});

_registerDeltaOperations('worktree_merge_progress', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    if (typeof diffReceiveMergeProgress === 'function') {
      diffReceiveMergeProgress(op);
    }
  },
});

_registerDeltaOperations('provider_usage', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    if (!state.provider_usage || typeof state.provider_usage !== 'object') {
      state.provider_usage = {};
    }
    var provider = String(
      op.provider || op.provider_id || op.adapter || op.name || ''
    ).trim();
    if (!provider && op.provider_usage && typeof op.provider_usage === 'object') {
      state.provider_usage = Object.assign({}, op.provider_usage);
    } else if (provider) {
      if (op.delete || op.remove || op.value === null) {
        delete state.provider_usage[provider];
      } else {
        var usagePayload = op.usage || op.value || op.payload || op.data || null;
        if (!usagePayload || typeof usagePayload !== 'object') {
          usagePayload = Object.assign({}, op);
          delete usagePayload.op;
          delete usagePayload.provider;
          delete usagePayload.provider_id;
          delete usagePayload.adapter;
          delete usagePayload.name;
        }
        state.provider_usage[provider] = usagePayload;
      }
    } else {
      var usageMap = Object.assign({}, op);
      delete usageMap.op;
      Object.assign(state.provider_usage, usageMap);
    }
    if (typeof refreshStatusBar === 'function') {
      refreshStatusBar({ providerUsage: true });
    }
  },
});

function _replaceDeltaObjectState(key, op) {
  var payload = Object.assign({}, op);
  delete payload.op;
  if (state[key] && typeof state[key] === 'object') {
    for (var existingKey in state[key]) {
      if (Object.prototype.hasOwnProperty.call(state[key], existingKey)
          && !Object.prototype.hasOwnProperty.call(payload, existingKey)) {
        delete state[key][existingKey];
      }
    }
    Object.assign(state[key], payload);
  } else {
    state[key] = payload;
  }
}

_registerDeltaOperations('runtime', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    _replaceDeltaObjectState('runtime', op);
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
    if (typeof supervisorReceiveRuntime === 'function') {
      supervisorReceiveRuntime(state.runtime && state.runtime.supervisor);
    }
  },
});

_registerDeltaOperations('relay_connection', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    _replaceDeltaObjectState('relay_connection', op);
    if (typeof refreshRelayStatusIndicator === 'function') {
      refreshRelayStatusIndicator();
    }
    if (typeof refreshStatusBar === 'function') {
      refreshStatusBar({ relay: true });
    }
  },
});

_registerDeltaOperations('relay_config', {
  invalidate: _noBroadSurfaceInvalidation,
  apply: function(op) {
    _replaceDeltaObjectState('relay_config', op);
    if (typeof refreshRelayConfigModal === 'function') {
      refreshRelayConfigModal();
    }
  },
});
