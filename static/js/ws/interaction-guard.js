/* Delta render interaction guard. */

// Track whether the user is actively interacting with the DOM in a way
// that a delta-driven rerender would interrupt. Two distinct interaction
// modes share one flag:
//
// (a) Pointer press: pointerdown..pointerup window. While pressing, defer
//     DOM-replacing renders so the press target survives long enough for
//     the browser to fire the synthetic click on mouseup. Without this,
//     a delta firehose (e.g. worker mcp_call_append from :224)
//     rerenders between pointerdown and pointerup, swapping the target
//     out and silently suppressing the click.
//
// (b) Text input typing / IME composition: keydown..keyup window inside
//     a text field, plus compositionstart..compositionend for IME. The
//     panel renderer captures `selectionStart`/`selectionEnd` BEFORE the
//     innerHTML rebuild and restores them AFTER, but the value captured
//     reflects state BEFORE the keystroke that's still in flight; if the
//     browser is mid-keystroke when the rebuild runs, the new node is
//     reattached but the in-flight char/composition is lost. Deferring
//     renders during the keystroke window avoids the race entirely.
//
// Critical: the post-interaction flush must NOT run inside the capture
// phase of the closing event (pointerup, keyup, compositionend), because
// replacing DOM there changes the event's target before the browser
// emits the synthetic follow-up (click for pointer, input for keystroke)
// — same suppression by a different path. We schedule the flush in a
// microtask / rAF after the closing event so the browser delivers the
// follow-up on the original target first, then deferred renders apply.
var _userPressing = false;  // legacy name; covers pointer + keyboard interaction now
// TORQUE:264 follow-up: hover-defer for agent-card tooltips. Agent cards expose
// their full status / activity / branch text via a CSS `:hover::after`
// pseudo-element on `.agent-card-tooltip` (style.css:1142). When the user
// hovers an active card and the worker fires events at firehose rate, the
// surface invalidation pipeline blasts `main.innerHTML` and destroys the
// hovered card mid-`:hover`; the tooltip vanishes, the new card appears, the
// pointer re-enters, the tooltip reappears — visible flicker. Defer
// DOM-replacing renders while the user is hovering an agent-card tooltip so
// the card DOM survives and the pseudo-element stays painted. Released on
// pointerout when the pointer leaves the tooltip subtree, with the same
// post-release flush schedule as pointerup / keyup.
var _userHovering = false;
function _userInteracting() {
  return !!(_userPressing || _userHovering);
}
var _postPressFlushScheduled = false;
function _flushAfterPress() {
  _postPressFlushScheduled = false;
  if (_userInteracting()) return;
  if (_pendingDeltaSurfaceInvalidations) _flushDeltaSurfaceRenderBatch();
}
function _schedulePostPressFlush() {
  if (_postPressFlushScheduled) return;
  _postPressFlushScheduled = true;
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(_flushAfterPress);
  } else if (typeof setTimeout === 'function') {
    setTimeout(_flushAfterPress, 0);
  } else {
    _flushAfterPress();
  }
}
// Terminal-flicker Phase 1: distinguish a typing release from a pointer
// release. Pointer releases keep the immediate next-frame flush (a click needs
// its post-press paint promptly). A typing release instead schedules a trailing
// idle flush so continuous typing coalesces to ~4 flushes/sec instead of one
// full render pass per keystroke — otherwise the delta-render clock syncs to
// the user's typing while an agent streams output underneath the composer.
var _userPressingFromKey = false;  // true while the current press came from typing
var _typingTrailingFlushTimer = 0;
var TYPING_TRAILING_FLUSH_MS = 250;
function _clearTypingTrailingFlush() {
  if (_typingTrailingFlushTimer && typeof clearTimeout === 'function') {
    clearTimeout(_typingTrailingFlushTimer);
  }
  _typingTrailingFlushTimer = 0;
}
function _scheduleTypingTrailingFlush() {
  // Re-armed on every keystroke: the pending batch only lands once typing
  // pauses (~250 ms), never between a keydown and its `input` event, so the
  // in-flight character is never dropped by a mid-press DOM swap.
  _clearTypingTrailingFlush();
  if (typeof setTimeout !== 'function') {
    _schedulePostPressFlush();
    return;
  }
  _typingTrailingFlushTimer = setTimeout(function() {
    _typingTrailingFlushTimer = 0;
    if (_userInteracting()) return;
    if (_pendingDeltaSurfaceInvalidations) _flushDeltaSurfaceRenderBatch();
  }, TYPING_TRAILING_FLUSH_MS);
}
// Selector for surfaces whose DOM identity we want to preserve while the
// user's pointer is over them. Currently the agent-card tooltip is the only
// CSS-pseudo-element-keyed surface in the grid; extend this list rather
// than adding new flags if more `:hover`-driven surfaces appear.
var _TORQUE_HOVER_DEFER_SELECTOR = '.agent-card-tooltip';
function _eventTargetInHoverSurface(target) {
  if (!target || typeof target.closest !== 'function') return false;
  return !!target.closest(_TORQUE_HOVER_DEFER_SELECTOR);
}
function _hoverEdgeIsBetweenTooltips(ev) {
  // pointerover / pointerout fire on every descendant transition. We only
  // care about the boundary where the pointer enters / leaves the
  // tooltip element itself — moving between two children of the same
  // tooltip must not toggle the flag (would thrash defer on/off mid-hover).
  if (!ev || !ev.target || typeof ev.target.closest !== 'function') return false;
  var fromTooltip = ev.target.closest(_TORQUE_HOVER_DEFER_SELECTOR);
  if (!fromTooltip) return false;
  var related = ev.relatedTarget || null;
  if (related && typeof related.closest === 'function') {
    var toTooltip = related.closest(_TORQUE_HOVER_DEFER_SELECTOR);
    if (toTooltip === fromTooltip) return false;
  }
  return true;
}
// True when the current event target is a text input / textarea /
// contenteditable surface. We only want to gate renders on keydown when
// the user is actively editing a text field — global hotkeys (e.g. Cmd+B,
// Tab) should still allow renders to proceed.
function _isTextEditingTarget(target) {
  if (!target || typeof target !== 'object') return false;
  var tag = String(target.tagName || '').toLowerCase();
  if (tag === 'textarea') return true;
  if (tag === 'input') {
    var type = String(target.type || 'text').toLowerCase();
    // Treat any non-button-like input as text-editing.
    return type !== 'button' && type !== 'submit' && type !== 'reset'
      && type !== 'checkbox' && type !== 'radio'
      && type !== 'file' && type !== 'image';
  }
  return !!target.isContentEditable;
}
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  var _userPressEnd = function(opts) {
    var wasInteracting = _userInteracting();
    var fromKey = _userPressingFromKey;
    _userPressing = false;
    _userPressingFromKey = false;
    if (wasInteracting && !_userInteracting() && _pendingDeltaSurfaceInvalidations) {
      // Defer flush so the browser delivers the synthetic follow-up
      // (click for pointer, input for keystroke) on the original target
      // before we swap DOM. Typing releases defer to a trailing idle flush so
      // continuous typing coalesces; pointer releases and the blur safety net
      // (opts.immediate) flush on the next frame.
      if (fromKey && !(opts && opts.immediate)) {
        _scheduleTypingTrailingFlush();
      } else {
        _schedulePostPressFlush();
      }
    }
  };
  var _userHoverEnd = function() {
    var wasInteracting = _userInteracting();
    _userHovering = false;
    if (wasInteracting && !_userInteracting() && _pendingDeltaSurfaceInvalidations) {
      _schedulePostPressFlush();
    }
  };
  document.addEventListener('pointerdown', function() {
    _userPressing = true;
    _userPressingFromKey = false;
  }, true);
  document.addEventListener('pointerup', _userPressEnd, true);
  document.addEventListener('pointercancel', _userPressEnd, true);
  // Keyboard typing in text fields: gate renders for the keystroke
  // window so the in-flight character isn't dropped by an innerHTML
  // rebuild between keydown and the corresponding `input` event.
  document.addEventListener('keydown', function(ev) {
    if (_isTextEditingTarget(ev && ev.target)) {
      _userPressing = true;
      _userPressingFromKey = true;
      // A fresh keystroke re-arms the trailing flush (via the matching keyup)
      // so continuous typing keeps deferring instead of leaking a flush.
      _clearTypingTrailingFlush();
    }
  }, true);
  document.addEventListener('keyup', _userPressEnd, true);
  // IME composition: composition events span multiple keystrokes for
  // CJK / accented input. The renderer must not swap DOM mid-composition
  // (composer state lives on the editing element and dies with the node).
  document.addEventListener('compositionstart', function() {
    _userPressing = true;
    _userPressingFromKey = true;
    _clearTypingTrailingFlush();
  }, true);
  document.addEventListener('compositionend', _userPressEnd, true);
  // Hover defer: pointerover fires when the pointer enters the tooltip
  // subtree (relatedTarget is outside it); pointerout fires when it leaves
  // (relatedTarget is outside). Inner-descendant transitions are filtered
  // by `_hoverEdgeIsBetweenTooltips` so the flag doesn't thrash.
  document.addEventListener('pointerover', function(ev) {
    if (!_hoverEdgeIsBetweenTooltips(ev)) return;
    if (_eventTargetInHoverSurface(ev.target)) _userHovering = true;
  }, true);
  document.addEventListener('pointerout', function(ev) {
    if (!_hoverEdgeIsBetweenTooltips(ev)) return;
    // The edge is "outbound" when the new target is not inside the same
    // tooltip — defined by `_hoverEdgeIsBetweenTooltips` returning true
    // only on real boundary crossings. Release the flag.
    _userHoverEnd();
  }, true);
  // Safety net: clear the flag on blur in case the closing event is
  // missed (e.g. capture lost mid-press, focus stolen mid-composition).
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('blur', function() {
      // Flush promptly on blur: cancel any pending trailing-typing timer and
      // fall back to the next-frame flush so a leaked batch can't strand.
      _clearTypingTrailingFlush();
      _userPressEnd({ immediate: true });
      _userHoverEnd();
      if (!_userInteracting() && _pendingDeltaSurfaceInvalidations) {
        _schedulePostPressFlush();
      }
    });
  }
}
