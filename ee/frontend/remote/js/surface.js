/* Rerender-stability helpers for the remote web UI.
 *
 * Copied and trimmed from static/js/render.js (_captureSurfaceState /
 * _restoreSurfaceState + their two private resolvers). The board-specific FLIP
 * animation and drag-rect helpers are intentionally omitted — the remote
 * conversation surface only needs focus/caret/scroll preservation across the
 * inbound-message rerenders.
 *
 * Behaviour is kept byte-for-byte equivalent to the operator app so the
 * regression intuition (and the {preventScroll:true} fix from
 * frontend_render_surface_focus) carries over. Exposed on globalThis as
 * RemoteSurface for both the browser bundle and the Node vm test harness.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;
  var doc = (typeof document !== 'undefined') ? document : (root.document || null);

  function _findSurfaceNode(node, selector) {
    if (!node || !selector) return null;
    if (selector === ':root') return node;
    if (selector.charAt(0) === '#' && doc && doc.getElementById) {
      return doc.getElementById(selector.slice(1));
    }
    return node.querySelector ? node.querySelector(selector) : null;
  }

  function _surfaceContains(node, el) {
    if (!node || !el) return false;
    if (typeof node.contains === 'function' && node.contains(el)) return true;
    if (!el.id) return false;
    return _findSurfaceNode(node, '#' + el.id) === el;
  }

  function _captureSurfaceState(node, opts) {
    if (!node) return null;
    opts = opts || {};
    var snapshot = { focus: null, scrolls: [] };
    var active = doc ? doc.activeElement : null;
    if (active && _surfaceContains(node, active)) {
      var key = active.id ? ('#' + active.id) : '';
      if (!key && typeof opts.captureFocusKey === 'function') {
        key = opts.captureFocusKey(active, node) || '';
      }
      if (key) {
        snapshot.focus = {
          key: key,
          value: 'value' in active ? active.value : null,
          checked: 'checked' in active ? !!active.checked : null,
          selectionStart: typeof active.selectionStart === 'number' ? active.selectionStart : null,
          selectionEnd: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
          scrollTop: typeof active.scrollTop === 'number' ? active.scrollTop : null,
          scrollLeft: typeof active.scrollLeft === 'number' ? active.scrollLeft : null,
        };
      }
    }
    var selectors = opts.scrollSelectors || [];
    for (var i = 0; i < selectors.length; i++) {
      var el = _findSurfaceNode(node, selectors[i]);
      if (!el) continue;
      snapshot.scrolls.push({
        selector: selectors[i],
        top: typeof el.scrollTop === 'number' ? el.scrollTop : null,
        left: typeof el.scrollLeft === 'number' ? el.scrollLeft : null,
      });
    }
    if (typeof opts.capture === 'function') opts.capture(snapshot, node);
    return snapshot;
  }

  function _restoreSurfaceState(node, snapshot, opts) {
    if (!node || !snapshot) return;
    opts = opts || {};
    for (var i = 0; i < snapshot.scrolls.length; i++) {
      var saved = snapshot.scrolls[i];
      var el = _findSurfaceNode(node, saved.selector);
      if (!el) continue;
      if (typeof saved.top === 'number') el.scrollTop = saved.top;
      if (typeof saved.left === 'number') el.scrollLeft = saved.left;
    }
    if (typeof opts.restore === 'function') opts.restore(node, snapshot);
    if (!snapshot.focus) return;
    var target = _findSurfaceNode(node, snapshot.focus.key);
    if (!target && typeof opts.resolveFocus === 'function') {
      target = opts.resolveFocus(node, snapshot.focus);
    }
    if (!target) return;
    // Skip re-assigning an unchanged value: setting el.value on a focused
    // textarea resets the caret/selection and briefly kills the visible cursor
    // under high rerender rates. We still re-assert focus + selection below.
    var valueDrifted = snapshot.focus.value != null
      && 'value' in target
      && target.value !== snapshot.focus.value;
    var checkedDrifted = snapshot.focus.checked != null
      && 'checked' in target
      && target.checked !== !!snapshot.focus.checked;
    if (valueDrifted) target.value = snapshot.focus.value;
    if (checkedDrifted) target.checked = snapshot.focus.checked;
    // preventScroll so re-render-driven focus restoration doesn't override the
    // explicit scroll restoration above (browser auto-scroll-into-view-on-focus
    // otherwise drags an offscreen composer back into view).
    if (typeof target.focus === 'function') {
      try { target.focus({ preventScroll: true }); }
      catch (_e) { target.focus(); }
    }
    if (typeof snapshot.focus.selectionStart === 'number' && 'selectionStart' in target) {
      target.selectionStart = snapshot.focus.selectionStart;
    }
    if (typeof snapshot.focus.selectionEnd === 'number' && 'selectionEnd' in target) {
      target.selectionEnd = snapshot.focus.selectionEnd;
    }
    // Restore the focused element's own scrollTop last — assigning value or
    // selection resets internal scroll, which would jump a multi-line compose
    // box back to the top on every rerender.
    if (typeof snapshot.focus.scrollTop === 'number' && typeof target.scrollTop === 'number') {
      target.scrollTop = snapshot.focus.scrollTop;
    }
    if (typeof snapshot.focus.scrollLeft === 'number' && typeof target.scrollLeft === 'number') {
      target.scrollLeft = snapshot.focus.scrollLeft;
    }
  }

  root.RemoteSurface = {
    captureSurfaceState: _captureSurfaceState,
    restoreSurfaceState: _restoreSurfaceState,
    _findSurfaceNode: _findSurfaceNode,
    _surfaceContains: _surfaceContains,
  };
})();
