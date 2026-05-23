/* Rendering for the remote web UI: agent list, conversation, ask cards,
 * composer, connection banner.
 *
 * Split into pure HTML builders (string in -> HTML string out, trivially unit
 * testable without a DOM) plus a thin paint orchestrator that applies the
 * innerHTML-cache discipline (shared-memory bc9e1d0f9f66): cache the rendered
 * HTML on the container and skip the repaint when unchanged; apply dynamic
 * state ONLY at the container's own dataset/style, never by mutating cached
 * descendants. Surface capture/restore wraps each repaint so the composer draft
 * + scroll survive inbound-message rerenders.
 *
 * Ask cards are GATED behind cfg.askEnabled (plan greenlit option (i)): the ask
 * UI stays dark until TORQUE:534 + the connector user-destination egress gating
 * land. The UI NEVER filters ask recipients itself — user-destination is
 * resolved server-side; this flag only toggles whether we render/answer the
 * already-resolved user-destined ask stream.
 *
 * Exposed on globalThis as RemoteRender for the browser bundle + Node tests.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;
  var md = root.torqueRenderMarkdownMessage;
  var esc = root.torqueMarkdownEscape || function(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

  function _body(text) {
    var renderer = root.torqueRenderMarkdownMessage || md;
    if (typeof renderer === 'function') return renderer(text);
    return '<p>' + esc(text) + '</p>';
  }

  var DELIVERY_GLYPH = {
    pending: '·', delivered: '✓', acked: '✓✓', failed: '✗',
  };

  function agentListHtml(store) {
    var agents = store.agentList();
    if (!agents.length) {
      return '<div class="remote-agent-empty">No conversations yet. Agents appear here'
        + ' when they message you.</div>';
    }
    var rows = agents.map(function(a) {
      var badges = '';
      if (a.pendingAskCount) {
        badges += '<span class="remote-badge remote-badge-ask">' + a.pendingAskCount + ' ask</span>';
      }
      if (a.unread) {
        badges += '<span class="remote-badge remote-badge-unread">' + a.unread + '</span>';
      }
      return '<button type="button" class="remote-agent-item'
        + (a.active ? ' is-active' : '') + '" data-agent-id="' + esc(a.agentId) + '">'
        + '<span class="remote-agent-name">' + esc(a.agentName) + '</span>'
        + '<span class="remote-agent-preview">' + esc((a.lastBody || '').slice(0, 80)) + '</span>'
        + badges + '</button>';
    });
    return rows.join('');
  }

  function messageHtml(msg) {
    var cls = 'remote-msg remote-msg-' + (msg.sender === 'user' ? 'user' : 'agent');
    if (msg.messageType === 'ask') cls += ' remote-msg-ask';
    var meta = '';
    if (msg.sender === 'user') {
      var glyph = DELIVERY_GLYPH[msg.deliveryState] || '';
      meta = '<span class="remote-msg-state remote-msg-state-' + esc(msg.deliveryState || '')
        + '">' + glyph + '</span>';
    }
    return '<div class="' + cls + '" data-msg-id="' + esc(msg.id) + '">'
      + '<div class="remote-msg-body">' + _body(msg.body) + '</div>'
      + meta + '</div>';
  }

  function conversationHtml(conv, cfg) {
    if (!conv) return '<div class="remote-conv-empty">Select a conversation.</div>';
    var html = conv.messages.map(function(msg) {
      if (msg.messageType === 'ask' && (!cfg || !cfg.askEnabled)) {
        // Ask UI gated off: render the ask body as a normal agent message so the
        // conversation stays coherent, but without the answerable card.
        var plain = { id: msg.id, sender: 'agent', body: msg.body,
          messageType: 'message', deliveryState: msg.deliveryState };
        return messageHtml(plain);
      }
      if (msg.messageType === 'ask' && cfg && cfg.askEnabled) {
        return askCardHtml(msg);
      }
      return messageHtml(msg);
    });
    return html.join('');
  }

  function askCardHtml(ask) {
    return '<div class="remote-ask-card" data-ask-id="' + esc(ask.id) + '">'
      + '<div class="remote-ask-label">Asking you</div>'
      + '<div class="remote-msg-body">' + _body(ask.body) + '</div>'
      + '<form class="remote-ask-form" data-ask-reply-for="' + esc(ask.id) + '">'
      + '<textarea class="remote-ask-input" data-ask-input="' + esc(ask.id)
      + '" rows="2" placeholder="Reply…"></textarea>'
      + '<button type="submit" class="remote-ask-send">Reply</button>'
      + '</form></div>';
  }

  function bannerHtml(status, detail) {
    detail = detail || {};
    var map = {
      idle: ['', ''],
      connecting: ['connecting', 'Connecting…'],
      ready: ['ready', ''],
      reconnecting: ['reconnecting', 'Reconnecting…'],
      reauth_required: ['reauth', 'Session expired — re-pair this device (scan the QR again).'],
      closed: ['closed', 'Disconnected.'],
    };
    var entry = map[status] || ['', ''];
    if (!entry[1]) return '';
    return '<div class="remote-banner remote-banner-' + entry[0] + '" role="status">'
      + esc(entry[1]) + '</div>';
  }

  // History affordance. When a snapshot has been applied (TORQUE:578), recent
  // context is shown and no notice is needed. When no/empty snapshot arrived
  // (e.g. :578 not deployed, or a local-dev relay), degrade gracefully to the
  // honest replay-only "showing new messages" notice (plan §11).
  function historyNoticeHtml(snapshotApplied) {
    if (snapshotApplied) return '';
    return '<div class="remote-history-notice">Showing new messages from when this'
      + ' device connected.</div>';
  }

  // Paint a sub-surface with the innerHTML cache discipline. Returns true if it
  // repainted, false if it skipped because the HTML was unchanged.
  function paintSurface(el, html, opts) {
    if (!el) return false;
    opts = opts || {};
    if (el._remoteLastHtml === html) {
      // Skip repaint to preserve interactive descendants (caret/scroll/focus).
      // Dynamic state lives on the container's OWN dataset (set by caller),
      // never on cached descendants — see bc9e1d0f9f66.
      return false;
    }
    var Surface = root.RemoteSurface;
    var snapshot = (Surface && opts.preserve)
      ? Surface.captureSurfaceState(el, opts.surfaceOpts || {}) : null;
    el.innerHTML = html;
    el._remoteLastHtml = html;
    if (snapshot && Surface) Surface.restoreSurfaceState(el, snapshot, opts.surfaceOpts || {});
    return true;
  }

  root.RemoteRender = {
    agentListHtml: agentListHtml,
    conversationHtml: conversationHtml,
    messageHtml: messageHtml,
    askCardHtml: askCardHtml,
    bannerHtml: bannerHtml,
    historyNoticeHtml: historyNoticeHtml,
    paintSurface: paintSurface,
  };
})();
