/* Shared, escape-first Markdown renderer for arbitrary message text.
 *
 * Security invariant: raw message text is HTML-escaped before any Markdown
 * transform runs. The transforms below only emit this small tag/attribute set:
 * p/br, strong/em, code/pre, a, ul/ol/li, h1-h6, blockquote, span. Raw HTML
 * in a message therefore remains inert escaped text, not browser-parsed markup.
 */
(function() {
  var root = (typeof globalThis !== 'undefined') ? globalThis : this;

  function _torqueMarkdownEscape(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function _torqueMarkdownToken(tokens, html) {
    var key = '\uE000torque-md-' + tokens.length + '\uE000';
    tokens.push({ key: key, html: html });
    return key;
  }

  function _torqueMarkdownRestoreTokens(text, tokens) {
    text = String(text || '');
    for (var i = 0; i < tokens.length; i++) {
      text = text.split(tokens[i].key).join(tokens[i].html);
    }
    return text;
  }

  function _torqueMarkdownExtractCode(text, tokens) {
    return String(text || '').replace(/`([^`\n]+)`/g, function(_match, code) {
      return _torqueMarkdownToken(tokens, '<code class="torque-md-inline-code">' + code + '</code>');
    });
  }

  function _torqueMarkdownSafeHref(escapedHref) {
    var href = String(escapedHref || '').trim();
    if (href.indexOf('&lt;') === 0 && href.slice(-4) === '&gt;') {
      href = href.slice(4, -4).trim();
    }
    if (!href || /\s/.test(href)) return '';
    if (/[\u0000-\u001f\u007f]/.test(href)) return '';
    var match = href.match(/^([A-Za-z][A-Za-z0-9+.-]*):/);
    if (!match) return '';
    var scheme = match[1].toLowerCase();
    if (scheme !== 'http' && scheme !== 'https' && scheme !== 'mailto') return '';
    return href;
  }

  function _torqueMarkdownEmphasis(text) {
    text = String(text || '');
    text = text.replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, '<strong>$2</strong>');
    text = text.replace(/(^|[^\w])([*_])(?=\S)([^*_\n]*?\S)\2(?=$|[^\w])/g, '$1<em>$3</em>');
    return text;
  }

  function _torqueMarkdownExtractLinks(text, tokens) {
    text = String(text || '');
    var out = '';
    var i = 0;
    while (i < text.length) {
      var open = text.indexOf('[', i);
      if (open < 0) {
        out += text.slice(i);
        break;
      }
      var close = text.indexOf(']', open + 1);
      if (close < 0 || text.charAt(close + 1) !== '(') {
        out += text.slice(i, open + 1);
        i = open + 1;
        continue;
      }
      var end = text.indexOf(')', close + 2);
      if (end < 0) {
        out += text.slice(i, open + 1);
        i = open + 1;
        continue;
      }
      out += text.slice(i, open);
      var label = text.slice(open + 1, close);
      var href = _torqueMarkdownSafeHref(text.slice(close + 2, end));
      var labelHtml = _torqueMarkdownRestoreTokens(_torqueMarkdownEmphasis(label), tokens) || href || 'link';
      if (href) {
        out += _torqueMarkdownToken(tokens,
          '<a class="torque-md-link" data-torque-markdown-link="1" href="' + href
          + '" target="_blank" rel="noopener noreferrer">' + labelHtml + '</a>');
      } else {
        out += _torqueMarkdownToken(tokens,
          '<span class="torque-md-link-disabled" title="Unsafe link removed">' + labelHtml + '</span>');
      }
      i = end + 1;
    }
    return out;
  }

  function _torqueMarkdownInline(escapedText) {
    var tokens = [];
    var text = _torqueMarkdownExtractCode(escapedText, tokens);
    text = _torqueMarkdownExtractLinks(text, tokens);
    text = _torqueMarkdownEmphasis(text);
    return _torqueMarkdownRestoreTokens(text, tokens);
  }

  function _torqueMarkdownIsFence(line) {
    var match = String(line || '').match(/^ {0,3}(```+|~~~+)/);
    return match ? match[1] : '';
  }

  function _torqueMarkdownIsBlockStart(line) {
    return /^ {0,3}#{1,6}\s+/.test(line)
      || /^ {0,3}> ?/.test(line)
      || /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line)
      || !!_torqueMarkdownIsFence(line);
  }

  function _torqueMarkdownParagraph(lines) {
    var parts = [];
    for (var i = 0; i < lines.length; i++) parts.push(_torqueMarkdownInline(lines[i]));
    return '<p>' + parts.join('<br>') + '</p>';
  }

  function torqueRenderMarkdownMessage(rawText) {
    var escaped = _torqueMarkdownEscape(rawText).replace(/\r\n?/g, '\n');
    var lines = escaped.split('\n');
    var html = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (!String(line || '').trim()) {
        i += 1;
        continue;
      }

      var fence = _torqueMarkdownIsFence(line);
      if (fence) {
        var codeLines = [];
        i += 1;
        while (i < lines.length && String(lines[i] || '').indexOf(fence) !== 0) {
          codeLines.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        html.push('<pre class="torque-md-code-block"><code>' + codeLines.join('\n') + '</code></pre>');
        continue;
      }

      var heading = String(line || '').match(/^ {0,3}(#{1,6})\s+(.+)$/);
      if (heading) {
        var level = heading[1].length;
        html.push('<h' + level + '>' + _torqueMarkdownInline(heading[2].replace(/\s+#+\s*$/, '')) + '</h' + level + '>');
        i += 1;
        continue;
      }

      if (/^ {0,3}> ?/.test(line)) {
        var quoteLines = [];
        while (i < lines.length && /^ {0,3}> ?/.test(lines[i])) {
          quoteLines.push(String(lines[i]).replace(/^ {0,3}> ?/, ''));
          i += 1;
        }
        html.push('<blockquote>' + _torqueMarkdownParagraph(quoteLines) + '</blockquote>');
        continue;
      }

      var listMatch = String(line || '').match(/^\s*((?:[-*+])|(?:\d+[.)]))\s+(.+)$/);
      if (listMatch) {
        var ordered = /\d/.test(listMatch[1]);
        var tag = ordered ? 'ol' : 'ul';
        var items = [];
        while (i < lines.length) {
          var itemMatch = String(lines[i] || '').match(ordered
            ? /^\s*\d+[.)]\s+(.+)$/
            : /^\s*[-*+]\s+(.+)$/);
          if (!itemMatch) break;
          items.push('<li>' + _torqueMarkdownInline(itemMatch[1]) + '</li>');
          i += 1;
        }
        html.push('<' + tag + '>' + items.join('') + '</' + tag + '>');
        continue;
      }

      var paragraphLines = [];
      while (i < lines.length && String(lines[i] || '').trim() && !_torqueMarkdownIsBlockStart(lines[i])) {
        paragraphLines.push(lines[i]);
        i += 1;
      }
      if (paragraphLines.length) html.push(_torqueMarkdownParagraph(paragraphLines));
      else i += 1;
    }
    return html.join('');
  }

  root.torqueRenderMarkdownMessage = torqueRenderMarkdownMessage;
  root.torqueMarkdownEscape = _torqueMarkdownEscape;
})();
