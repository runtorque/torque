/* Browser glue for the remote web UI: wires config -> store -> relay client ->
 * render. Thin on purpose — all testable logic lives in the modules it wires
 * together (config/envelope/store/relay_client/render_remote), which carry the
 * Node regression coverage. This file is the DOM-bound orchestration only.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;
  if (typeof document === 'undefined') return; // not in a browser

  function boot() {
    var cfg = root.RemoteConfig.loadFromDocument(document, location);
    var els = {
      banner: document.getElementById('remote-banner'),
      agentList: document.getElementById('remote-agent-list'),
      conversation: document.getElementById('remote-conversation'),
      composer: document.getElementById('remote-composer'),
      input: document.getElementById('remote-composer-input'),
      history: document.getElementById('remote-history-notice'),
    };

    if (!cfg.valid) {
      if (els.banner) {
        els.banner.innerHTML = root.RemoteRender.bannerHtml('closed')
          || '<div class="remote-banner remote-banner-closed">Missing config: '
          + cfg.errors.join(', ') + '</div>';
      }
      return;
    }

    if (!cfg.clientId) {
      cfg.clientId = _stableClientId();
    }

    // Banner state: the live connection status plus a transient relay-error
    // overlay. A generic relay `error` (onStatus('error', …)) shows over the
    // connection status; it clears on the next `ready` or the next inbound
    // store update (both signal the channel is alive again).
    var connStatus = root.RemoteRelayClient.STATUS.IDLE;
    var errorPayload = null;

    var store = new root.RemoteStore({});
    var client = new root.RemoteRelayClient({
      config: cfg,
      store: store,
      httpPost: _fetchPost,
      onStatus: function(status, detail) { renderBanner(status, detail); },
    });

    store.subscribe(function() {
      // Any store mutation (inbound/outbound/snapshot) means the channel is
      // live — clear a stale relay-error banner.
      if (errorPayload) { errorPayload = null; paintBanner(); }
      render();
    });

    function paintBanner() {
      if (!els.banner) return;
      els.banner.innerHTML = errorPayload
        ? root.RemoteRender.bannerHtml('error', { payload: errorPayload })
        : root.RemoteRender.bannerHtml(connStatus, {});
    }

    function render() {
      if (els.history) {
        els.history.innerHTML = root.RemoteRender.historyNoticeHtml(store.snapshotApplied);
      }
      if (els.agentList) {
        root.RemoteRender.paintSurface(els.agentList,
          root.RemoteRender.agentListHtml(store));
      }
      if (els.conversation) {
        // stickToBottom (chat tail): a new message auto-scrolls to bottom only
        // when the user was already at/near the tail; if they scrolled up, their
        // position is preserved. The stick container is the conversation pane
        // itself (':root'); it replaces a plain scrollSelectors anchor so the
        // two do not fight.
        root.RemoteRender.paintSurface(els.conversation,
          root.RemoteRender.conversationHtml(store.activeConversation(), cfg),
          { preserve: true, surfaceOpts: { stickToBottom: ':root' } });
      }
    }

    function renderBanner(status, detail) {
      if (status === 'error') {
        // Transient overlay; does not change the underlying connection status.
        errorPayload = (detail && detail.payload) || {};
        paintBanner();
        return;
      }
      connStatus = status;
      if (status === root.RemoteRelayClient.STATUS.READY) errorPayload = null;
      paintBanner();
      if (els.composer && els.input) {
        els.input.disabled = status !== root.RemoteRelayClient.STATUS.READY;
      }
    }

    // Agent switching (event delegation on the list).
    if (els.agentList) {
      els.agentList.addEventListener('click', function(ev) {
        var btn = ev.target && ev.target.closest
          ? ev.target.closest('[data-agent-id]') : null;
        if (btn) store.setActiveAgent(btn.getAttribute('data-agent-id'));
      });
    }

    // Composer submit -> user_message to the active agent.
    if (els.composer) {
      els.composer.addEventListener('submit', function(ev) {
        ev.preventDefault();
        var conv = store.activeConversation();
        if (!conv || !els.input) return;
        var text = String(els.input.value || '').trim();
        if (!text) return;
        client.sendUserMessage(conv.agentId, text, { threadId: '' });
        els.input.value = '';
      });
    }

    // Ask answers (gated): submit an ask reply as a user_message + reply_to_id.
    if (els.conversation && cfg.askEnabled) {
      els.conversation.addEventListener('submit', function(ev) {
        var form = ev.target && ev.target.closest
          ? ev.target.closest('[data-ask-reply-for]') : null;
        if (!form) return;
        ev.preventDefault();
        var askId = form.getAttribute('data-ask-reply-for');
        var conv = store.activeConversation();
        var input = form.querySelector ? form.querySelector('[data-ask-input]') : null;
        var text = input ? String(input.value || '').trim() : '';
        if (!conv || !text) return;
        client.sendUserMessage(conv.agentId, text, { replyToId: askId });
        if (input) input.value = '';
      });
    }

    render();
    client.connect();
    root.__torqueRemote = { cfg: cfg, store: store, client: client };
  }

  function _stableClientId() {
    var key = 'torque_remote_client_id';
    try {
      var existing = root.localStorage && root.localStorage.getItem(key);
      if (existing) return existing;
      var id = (root.RemoteEnvelope ? root.RemoteEnvelope.newId('client') : 'client-' + Date.now());
      if (root.localStorage) root.localStorage.setItem(key, id);
      return id;
    } catch (_e) {
      return 'client-' + Date.now().toString(36);
    }
  }

  function _fetchPost(url, headers, body) {
    if (typeof fetch === 'undefined') return Promise.resolve(null);
    return fetch(url, {
      method: 'POST',
      headers: headers,
      body: body,
      credentials: 'include', // carry the HttpOnly torque_session cookie
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
