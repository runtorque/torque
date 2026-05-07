/* Tauri native API shim. Safe no-op fallbacks keep browser/pywebview alive. */
(function(global) {
  function tauriInvoke() {
    if (global.__TAURI__ && global.__TAURI__.core && typeof global.__TAURI__.core.invoke === 'function') {
      return global.__TAURI__.core.invoke;
    }
    if (global.__TAURI__ && typeof global.__TAURI__.invoke === 'function') return global.__TAURI__.invoke;
    return null;
  }

  function invoke(command, args) {
    var fn = tauriInvoke();
    if (!fn) return Promise.reject(new Error('Native desktop API is unavailable in this runtime.'));
    return Promise.resolve(fn(command, args || {}));
  }

  function toastError(error, fallback) {
    var message = (error && (error.message || String(error))) || fallback || 'Native desktop action failed';
    if (typeof _showToast === 'function') _showToast(message, 'error');
    else if (typeof console !== 'undefined' && console.warn) console.warn(message);
    throw error;
  }

  var api = {
    available: function() { return !!tauriInvoke(); },
    invoke: invoke,
    detach: function(panel, bounds) {
      return invoke('detach', { panel: panel, bounds: bounds || null })
        .catch(function(error) { return toastError(error, 'Unable to detach panel'); });
    },
    focusWindow: function(label) {
      return invoke('focus_window', { label: label })
        .catch(function(error) { return toastError(error, 'Unable to focus detached panel'); });
    },
    reattach: function(label) {
      return invoke('reattach', { label: label })
        .catch(function(error) { return toastError(error, 'Unable to reattach panel'); });
    },
    reattachAll: function() {
      return invoke('reattach_all')
        .catch(function(error) { return toastError(error, 'Unable to reattach panels'); });
    },
    listDetached: function() {
      return invoke('list_detached').catch(function() { return []; });
    },
    currentWindowBounds: function() {
      return invoke('current_window_bounds').catch(function() { return null; });
    },
    revealLogDir: function() {
      return invoke('reveal_log_dir')
        .catch(function(error) { return toastError(error, 'Unable to reveal logs folder'); });
    },
    openExternal: function(url) {
      return invoke('open_external', { url: url })
        .catch(function(error) { return toastError(error, 'Unable to open external link'); });
    },
    runtimeConfig: function() {
      return invoke('runtime_config').catch(function() { return null; });
    },
  };

  global.nativeApi = api;
})(window);
