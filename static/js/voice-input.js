/**
 * Voice-to-Text Input Module
 *
 * WhatsApp-style push-to-talk voice input for textareas.
 * Auto-attaches to any <textarea data-voice-enabled>.
 * Uses the browser-native Web Speech API — no backend required.
 */
(function () {
  "use strict";

  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    return;
  }

  var MAX_RECORDING_SECONDS = 120;

  function VoiceRecorder(opts) {
    this._recording = false;
    this._chunks = [];
    this._onResult = opts.onResult || function () {};
    this._onError = opts.onError || function () {};
    this._recognition = null;
  }

  Object.defineProperty(VoiceRecorder.prototype, "recording", {
    get: function () {
      return this._recording;
    },
  });

  VoiceRecorder.prototype._ensureRecognition = function () {
    if (this._recognition) return;

    this._recognition = new SpeechRecognition();
    this._recognition.continuous = true;
    this._recognition.interimResults = true;
    this._recognition.lang = document.documentElement.lang || "en-US";

    var self = this;

    this._recognition.onresult = function (event) {
      for (var i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          self._chunks.push(event.results[i][0].transcript);
        }
      }
    };

    this._recognition.onerror = function (event) {
      if (event.error === "aborted") {
        return;
      }
      var text = self._chunks.join(" ").trim();
      self._recording = false;
      if (text) {
        self._onResult(text);
      }
      self._onError(event.error || "unknown");
    };

    this._recognition.onend = function () {
      if (self._recording) {
        self._recording = false;
        var text = self._chunks.join(" ").trim();
        if (text) {
          self._onResult(text);
        }
      }
    };
  };

  VoiceRecorder.prototype.start = function () {
    this._ensureRecognition();
    this._chunks = [];
    this._recording = true;
    try {
      this._recognition.start();
    } catch (e) {
      // Already started.
    }
  };

  VoiceRecorder.prototype.stop = function () {
    if (!this._recording) return;
    try {
      this._recognition.stop();
    } catch (e) {
      this._recording = false;
    }
  };

  var SVG_NS = "http://www.w3.org/2000/svg";

  function createMicSvg(filled) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", filled ? "currentColor" : "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", filled ? "1" : "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("class", filled ? "w-6 h-6" : "w-5 h-5");
    svg.setAttribute("aria-hidden", "true");

    var path1 = document.createElementNS(SVG_NS, "path");
    path1.setAttribute("d", "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z");
    svg.appendChild(path1);

    var path2 = document.createElementNS(SVG_NS, "path");
    path2.setAttribute("d", "M19 10v2a7 7 0 0 1-14 0v-2");
    svg.appendChild(path2);

    var line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", "12");
    line.setAttribute("x2", "12");
    line.setAttribute("y1", "19");
    line.setAttribute("y2", "22");
    svg.appendChild(line);

    return svg;
  }

  var stylesInjected = false;

  function injectStyles() {
    if (stylesInjected) return;
    stylesInjected = true;

    var style = document.createElement("style");
    style.textContent = [
      ".voice-input-wrap {",
      "  position: relative;",
      "  display: flex;",
      "  align-items: flex-end;",
      "  gap: 8px;",
      "  width: 100%;",
      "}",
      ".voice-textarea-shell {",
      "  position: relative;",
      "  display: flex;",
      "  flex: 1 1 auto;",
      "  min-width: 0;",
      "}",
      ".voice-textarea-shell textarea {",
      "  width: 100%;",
      "}",
      ".voice-input-controls {",
      "  display: flex;",
      "  align-items: center;",
      "  gap: 6px;",
      "  flex: 0 0 auto;",
      "}",
      ".voice-mic-btn {",
      "  position: static;",
      "  width: 40px;",
      "  height: 40px;",
      "  flex: 0 0 40px;",
      "  display: flex;",
      "  align-items: center;",
      "  justify-content: center;",
      "  border-radius: 8px;",
      "  background: transparent;",
      "  border: none;",
      "  color: #94a3b8;",
      "  cursor: pointer;",
      "  padding: 0;",
      "  transition: color 0.15s ease, background 0.15s ease;",
      "  touch-action: none;",
      "  user-select: none;",
      "  -webkit-user-select: none;",
      "}",
      ".voice-mic-btn:hover {",
      "  color: #475569;",
      "  background: rgba(148, 163, 184, 0.12);",
      "}",
      ".voice-mic-btn:focus-visible {",
      "  outline: 2px solid rgba(59, 130, 246, 0.5);",
      "  outline-offset: 2px;",
      "}",
      ".dark .voice-mic-btn {",
      "  color: #64748b;",
      "}",
      ".dark .voice-mic-btn:hover {",
      "  color: #cbd5e1;",
      "  background: rgba(100, 116, 139, 0.18);",
      "}",
      ".voice-ai-badge {",
      "  position: static;",
      "  display: inline-flex;",
      "  align-items: center;",
      "  justify-content: center;",
      "  min-width: 40px;",
      "  height: 40px;",
      "  border-radius: 8px;",
      "  border: 1px solid rgba(15, 118, 110, 0.22);",
      "  background: rgba(240, 253, 250, 0.96);",
      "  color: #115e59;",
      "  padding: 0 10px;",
      "  font-size: 0.6875rem;",
      "  font-weight: 700;",
      "  letter-spacing: 0;",
      "  line-height: 1;",
      "  white-space: nowrap;",
      "  pointer-events: auto;",
      "  cursor: pointer;",
      "  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;",
      "}",
      ".voice-ai-badge::before {",
      "  content: none;",
      "}",
      ".dark .voice-ai-badge {",
      "  border-color: rgba(45, 212, 191, 0.25);",
      "  background: rgba(15, 23, 42, 0.92);",
      "  color: #5eead4;",
      "}",
      ".voice-ai-badge:hover {",
      "  border-color: rgba(15, 118, 110, 0.4);",
      "  background: rgba(204, 251, 241, 0.98);",
      "}",
      ".dark .voice-ai-badge:hover {",
      "  border-color: rgba(45, 212, 191, 0.45);",
      "  background: rgba(22, 78, 99, 0.88);",
      "}",
      ".voice-ai-badge:focus-visible {",
      "  outline: 2px solid rgba(20, 184, 166, 0.45);",
      "  outline-offset: 2px;",
      "}",
      "@media (max-width: 480px) {",
      "  .voice-input-wrap {",
      "    gap: 6px;",
      "  }",
      "  .voice-input-controls {",
      "    align-self: flex-end;",
      "  }",
      "}",
      ".voice-overlay {",
      "  position: absolute;",
      "  inset: 0;",
      "  display: flex;",
      "  align-items: center;",
      "  justify-content: center;",
      "  gap: 16px;",
      "  background: #fef2f2;",
      "  border: 2px solid #fca5a5;",
      "  border-radius: inherit;",
      "  z-index: 20;",
      "  pointer-events: none;",
      "}",
      ".dark .voice-overlay {",
      "  background: rgba(127, 29, 29, 0.15);",
      "  border-color: #991b1b;",
      "}",
      ".voice-overlay-mic {",
      "  color: #ef4444;",
      "  animation: voice-pulse 1.5s ease-in-out infinite;",
      "}",
      "@keyframes voice-pulse {",
      "  0%, 100% { opacity: 1; transform: scale(1); }",
      "  50% { opacity: 0.6; transform: scale(1.12); }",
      "}",
      ".voice-waveform {",
      "  display: flex;",
      "  align-items: center;",
      "  gap: 3px;",
      "  height: 24px;",
      "}",
      ".voice-waveform > div {",
      "  width: 3px;",
      "  background: #f87171;",
      "  border-radius: 2px;",
      "  animation: voice-wave 0.8s ease-in-out infinite alternate;",
      "}",
      ".voice-waveform > div:nth-child(1) { animation-delay: 0s; height: 8px; }",
      ".voice-waveform > div:nth-child(2) { animation-delay: 0.1s; height: 16px; }",
      ".voice-waveform > div:nth-child(3) { animation-delay: 0.2s; height: 24px; }",
      ".voice-waveform > div:nth-child(4) { animation-delay: 0.3s; height: 16px; }",
      ".voice-waveform > div:nth-child(5) { animation-delay: 0.4s; height: 8px; }",
      "@keyframes voice-wave {",
      "  0% { transform: scaleY(0.4); }",
      "  100% { transform: scaleY(1); }",
      "}",
      ".voice-timer {",
      "  font-family: 'Outfit', sans-serif;",
      "  font-size: 0.875rem;",
      "  font-weight: 600;",
      "  font-variant-numeric: tabular-nums;",
      "  color: #dc2626;",
      "}",
      ".dark .voice-timer {",
      "  color: #f87171;",
      "}",
      ".voice-sr-only {",
      "  position: absolute;",
      "  width: 1px;",
      "  height: 1px;",
      "  padding: 0;",
      "  margin: -1px;",
      "  overflow: hidden;",
      "  clip: rect(0, 0, 0, 0);",
      "  white-space: nowrap;",
      "  border-width: 0;",
      "}",
      ".voice-suggestion-panel {",
      "  margin-top: 8px;",
      "  border-radius: 14px;",
      "  border: 1px solid #cbd5e1;",
      "  background: #f8fafc;",
      "  padding: 10px 12px;",
      "  color: #0f172a;",
      "}",
      ".dark .voice-suggestion-panel {",
      "  border-color: rgba(71, 85, 105, 0.85);",
      "  background: rgba(15, 23, 42, 0.72);",
      "  color: #e2e8f0;",
      "}",
      ".voice-suggestion-panel[hidden] {",
      "  display: none;",
      "}",
      ".voice-suggestion-header {",
      "  display: flex;",
      "  align-items: center;",
      "  justify-content: space-between;",
      "  gap: 12px;",
      "}",
      ".voice-suggestion-title {",
      "  font-size: 0.75rem;",
      "  font-weight: 700;",
      "  letter-spacing: 0.04em;",
      "  text-transform: uppercase;",
      "  color: #475569;",
      "}",
      ".dark .voice-suggestion-title {",
      "  color: #94a3b8;",
      "}",
      ".voice-suggestion-status {",
      "  font-size: 0.75rem;",
      "  color: #64748b;",
      "}",
      ".dark .voice-suggestion-status {",
      "  color: #94a3b8;",
      "}",
      ".voice-suggestion-status-error {",
      "  color: #b91c1c;",
      "}",
      ".dark .voice-suggestion-status-error {",
      "  color: #fca5a5;",
      "}",
      ".voice-suggestion-text {",
      "  margin-top: 8px;",
      "  font-size: 0.875rem;",
      "  line-height: 1.5;",
      "  white-space: pre-wrap;",
      "}",
      ".voice-suggestion-actions {",
      "  display: flex;",
      "  flex-wrap: wrap;",
      "  gap: 8px;",
      "  margin-top: 10px;",
      "}",
      ".voice-suggestion-btn {",
      "  border: 1px solid #cbd5e1;",
      "  border-radius: 9999px;",
      "  background: #fff;",
      "  color: #334155;",
      "  padding: 6px 10px;",
      "  font-size: 0.75rem;",
      "  font-weight: 600;",
      "  cursor: pointer;",
      "}",
      ".voice-suggestion-btn:hover {",
      "  background: #f8fafc;",
      "}",
      ".voice-suggestion-btn-primary {",
      "  border-color: #0f766e;",
      "  background: #0f766e;",
      "  color: #fff;",
      "}",
      ".voice-suggestion-btn-primary:hover {",
      "  background: #115e59;",
      "}",
      ".dark .voice-suggestion-btn {",
      "  border-color: #475569;",
      "  background: rgba(15, 23, 42, 0.8);",
      "  color: #e2e8f0;",
      "}",
      ".dark .voice-suggestion-btn:hover {",
      "  background: rgba(30, 41, 59, 0.95);",
      "}",
      ".dark .voice-suggestion-btn-primary {",
      "  border-color: #14b8a6;",
      "  background: #0f766e;",
      "  color: #f8fafc;",
      "}",
      ".voice-suggestion-alt-list {",
      "  display: flex;",
      "  flex-wrap: wrap;",
      "  gap: 8px;",
      "  margin-top: 8px;",
      "}",
      ".voice-suggestion-chip {",
      "  border: 1px dashed #94a3b8;",
      "  border-radius: 9999px;",
      "  background: transparent;",
      "  color: inherit;",
      "  padding: 5px 10px;",
      "  font-size: 0.75rem;",
      "  cursor: pointer;",
      "}",
      ".voice-suggestion-chip:hover {",
      "  border-color: #0f766e;",
      "  color: #0f766e;",
      "}",
      ".dark .voice-suggestion-chip {",
      "  border-color: #64748b;",
      "}",
      ".dark .voice-suggestion-chip:hover {",
      "  border-color: #2dd4bf;",
      "  color: #5eead4;",
      "}",
      "@media (prefers-reduced-motion: reduce) {",
      "  .voice-overlay-mic { animation: none; }",
      "  .voice-waveform > div { animation: none; }",
      "}",
    ].join("\n");
    document.head.appendChild(style);
  }

  function formatTime(seconds) {
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function createOverlay() {
    var overlay = document.createElement("div");
    overlay.className = "voice-overlay";

    var micWrap = document.createElement("div");
    micWrap.className = "voice-overlay-mic";
    micWrap.appendChild(createMicSvg(true));
    overlay.appendChild(micWrap);

    var waveform = document.createElement("div");
    waveform.className = "voice-waveform";
    for (var i = 0; i < 5; i++) {
      waveform.appendChild(document.createElement("div"));
    }
    overlay.appendChild(waveform);

    var timer = document.createElement("span");
    timer.className = "voice-timer";
    timer.textContent = "0:00";
    overlay.appendChild(timer);

    return overlay;
  }

  function normalizeTranscriptText(text) {
    var normalized = String(text || "").replace(/\s+/g, " ").trim();
    if (!normalized) {
      return "";
    }
    normalized = normalized.replace(/\s+([,.;!?])/g, "$1");
    normalized = normalized.replace(/^([a-z])/, function (match) {
      return match.toUpperCase();
    });
    if (!/[.!?…]$/.test(normalized)) {
      normalized += ".";
    }
    return normalized;
  }

  function joinTranscript(baseText, addition) {
    if (!baseText) {
      return addition;
    }
    return /\s$/.test(baseText) ? baseText + addition : baseText + " " + addition;
  }

  function createSuggestionPanel() {
    var panel = document.createElement("div");
    panel.className = "voice-suggestion-panel";
    panel.hidden = true;
    panel.innerHTML = [
      '<div class="voice-suggestion-header">',
      '  <div class="voice-suggestion-title">AI Polish</div>',
      '  <div class="voice-suggestion-status" data-voice-suggestion-status></div>',
      "</div>",
      '<div class="voice-suggestion-text" data-voice-suggestion-text></div>',
      '<div class="voice-suggestion-alt-list" data-voice-suggestion-alts hidden></div>',
      '<div class="voice-suggestion-actions" data-voice-suggestion-actions hidden>',
      '  <button type="button" class="voice-suggestion-btn voice-suggestion-btn-primary" data-voice-apply>',
      "    Use suggestion",
      "  </button>",
      '  <button type="button" class="voice-suggestion-btn" data-voice-keep>',
      "    Keep current",
      "  </button>",
      "</div>",
    ].join("");
    return panel;
  }

  function getSuggestionPanel(textarea, wrap) {
    if (textarea._voiceSuggestionPanel) {
      return textarea._voiceSuggestionPanel;
    }
    var panel = createSuggestionPanel();
    if (wrap.parentNode) {
      wrap.parentNode.insertBefore(panel, wrap.nextSibling);
    }
    textarea._voiceSuggestionPanel = panel;
    return panel;
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function attachVoiceInput(textarea) {
    if (textarea.dataset.voiceAttached === "true") return;
    textarea.dataset.voiceAttached = "true";

    var wrap = document.createElement("div");
    wrap.className = "voice-input-wrap";
    textarea.parentNode.insertBefore(wrap, textarea);

    var textareaShell = document.createElement("div");
    textareaShell.className = "voice-textarea-shell";
    wrap.appendChild(textareaShell);
    textareaShell.appendChild(textarea);

    var controls = document.createElement("div");
    controls.className = "voice-input-controls";
    wrap.appendChild(controls);

    var liveRegion = document.createElement("span");
    liveRegion.setAttribute("aria-live", "polite");
    liveRegion.className = "voice-sr-only";
    wrap.appendChild(liveRegion);

    var overlay = null;
    var timerInterval = null;
    var safetyTimeout = null;
    var elapsed = 0;
    var panel = null;
    var statusEl = null;
    var textEl = null;
    var actionsEl = null;
    var altListEl = null;
    var applyBtn = null;
    var keepBtn = null;
    var latestSuggestion = null;
    var suggestionPending = false;

    function ensureSuggestionPanel() {
      if (panel) {
        return true;
      }
      try {
        panel = getSuggestionPanel(textarea, wrap);
        statusEl = panel.querySelector("[data-voice-suggestion-status]");
        textEl = panel.querySelector("[data-voice-suggestion-text]");
        actionsEl = panel.querySelector("[data-voice-suggestion-actions]");
        altListEl = panel.querySelector("[data-voice-suggestion-alts]");
        applyBtn = panel.querySelector("[data-voice-apply]");
        keepBtn = panel.querySelector("[data-voice-keep]");
      } catch (_error) {
        panel = null;
      }
      return !!panel;
    }

    function hideSuggestionPanel() {
      setSuggestionPending(false);
      if (!panel) {
        latestSuggestion = null;
        return;
      }
      panel.hidden = true;
      latestSuggestion = null;
    }

    function showSuggestionError(baseText, segmentText, message) {
      if (!ensureSuggestionPanel()) {
        return;
      }
      setSuggestionPending(false);
      latestSuggestion = {
        baseText: baseText,
        segmentText: segmentText,
        combinedOriginal: joinTranscript(baseText, segmentText),
        mode: "segment",
      };
      panel.hidden = false;
      statusEl.textContent = message || "AI polish unavailable";
      statusEl.classList.add("voice-suggestion-status-error");
      textEl.textContent = segmentText || "";
      actionsEl.hidden = true;
      altListEl.hidden = true;
      clearChildren(altListEl);
    }

    function setSuggestionPending(isPending) {
      suggestionPending = !!isPending;
      if (aiBadge) {
        aiBadge.disabled = suggestionPending;
        aiBadge.textContent = suggestionPending ? "..." : "AI";
        aiBadge.setAttribute(
          "aria-label",
          suggestionPending ? "Polishing current text with AI" : "Polish current text with AI"
        );
      }
    }

    function updateTextareaValue(nextValue) {
      textarea.value = nextValue;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.focus();
    }

    function applySuggestedText(candidate) {
      if (!latestSuggestion || !candidate) {
        return;
      }
      var current = textarea.value || "";
      if (latestSuggestion.mode === "full") {
        updateTextareaValue(candidate);
        hideSuggestionPanel();
        return;
      }
      var expected = latestSuggestion.combinedOriginal;
      if (current === expected) {
        updateTextareaValue(joinTranscript(latestSuggestion.baseText, candidate));
        hideSuggestionPanel();
        return;
      }
      if (
        latestSuggestion.segmentText &&
        current.slice(-latestSuggestion.segmentText.length) === latestSuggestion.segmentText
      ) {
        updateTextareaValue(current.slice(0, -latestSuggestion.segmentText.length) + candidate);
        hideSuggestionPanel();
      }
    }

    function showSuggestionLoading(baseText, segmentText, mode) {
      if (!ensureSuggestionPanel()) {
        return;
      }
      setSuggestionPending(true);
      latestSuggestion = {
        baseText: baseText,
        segmentText: segmentText,
        combinedOriginal: joinTranscript(baseText, segmentText),
        mode: mode || "segment",
        originalRaw: textarea.value || "",
      };
      panel.hidden = false;
      statusEl.textContent = "Refining sentence...";
      textEl.textContent = segmentText;
      actionsEl.hidden = true;
      altListEl.hidden = true;
      clearChildren(altListEl);
    }

    function renderSuggestion(baseText, originalSegment, payload, mode) {
      if (!ensureSuggestionPanel()) {
        return;
      }
      setSuggestionPending(false);
      if (!payload || !payload.suggested_text) {
        hideSuggestionPanel();
        return;
      }
      latestSuggestion = {
        baseText: baseText,
        segmentText: originalSegment,
        combinedOriginal: joinTranscript(baseText, originalSegment),
        mode: mode || "segment",
        originalRaw: latestSuggestion && latestSuggestion.originalRaw ? latestSuggestion.originalRaw : textarea.value || "",
      };
      panel.hidden = false;
      statusEl.classList.remove("voice-suggestion-status-error");
      statusEl.textContent = payload.meta && payload.meta.provider ? payload.meta.provider + " suggestion" : "AI suggestion";
      textEl.textContent = payload.suggested_text;
      actionsEl.hidden = false;
      clearChildren(altListEl);
      var alternatives = Array.isArray(payload.alternatives) ? payload.alternatives : [];
      if (alternatives.length) {
        altListEl.hidden = false;
        for (var i = 0; i < alternatives.length; i++) {
          var altButton = document.createElement("button");
          altButton.type = "button";
          altButton.className = "voice-suggestion-chip";
          altButton.textContent = alternatives[i];
          altButton.addEventListener("click", (function (candidate) {
            return function () {
              applySuggestedText(candidate);
            };
          })(alternatives[i]));
          altListEl.appendChild(altButton);
        }
      } else {
        altListEl.hidden = true;
      }
      applyBtn.onclick = function () {
        applySuggestedText(payload.suggested_text);
      };
      keepBtn.onclick = function () {
        hideSuggestionPanel();
      };
    }

    function requestSuggestion(baseText, segmentText, mode) {
      if (!segmentText) {
        hideSuggestionPanel();
        return;
      }
      if (!ensureSuggestionPanel()) {
        return;
      }
      try {
        showSuggestionLoading(baseText, segmentText, mode);
        fetch("/admin/ai/voice/sentence-suggestion", {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": typeof getCsrfToken === "function" ? getCsrfToken() || "" : "",
          },
          body: JSON.stringify({
            text: segmentText,
            context: textarea.dataset.voiceContext || "",
          }),
        })
          .then(function (response) {
            return response.json();
          })
          .then(function (payload) {
            if (!payload || !payload.ok || !payload.suggested_text) {
              showSuggestionError(
                baseText,
                segmentText,
                payload && payload.error ? String(payload.error) : "AI polish unavailable"
              );
              return;
            }
            renderSuggestion(baseText, segmentText, payload, mode);
          })
          .catch(function () {
            showSuggestionError(baseText, segmentText, "AI request failed");
          });
      } catch (_error) {
        showSuggestionError(baseText, segmentText, "AI request failed");
      }
    }

    function showOverlay() {
      elapsed = 0;
      overlay = createOverlay();
      textareaShell.appendChild(overlay);
      var timerEl = overlay.querySelector(".voice-timer");
      timerInterval = setInterval(function () {
        elapsed++;
        if (timerEl) {
          timerEl.textContent = formatTime(elapsed);
        }
      }, 1000);
      liveRegion.textContent = "Recording";
    }

    function hideOverlay() {
      if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
      }
      if (overlay && overlay.parentNode) {
        overlay.parentNode.removeChild(overlay);
        overlay = null;
      }
    }

    var recorder = null;

    function ensureRecorder() {
      if (recorder) return;
      recorder = new VoiceRecorder({
        onResult: function (text) {
          var baseText = textarea.value || "";
          var cleanedText = normalizeTranscriptText(text);
          if (!cleanedText) {
            return;
          }
          var nextValue = joinTranscript(baseText, cleanedText);
          textarea.value = nextValue;
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
          liveRegion.textContent = "Voice text added";
          setTimeout(function () {
            liveRegion.textContent = "";
          }, 2000);
          hideSuggestionPanel();
        },
        onError: function (errMsg) {
          console.warn("Voice input error:", errMsg);
          liveRegion.textContent = "Voice input error";
          setTimeout(function () {
            liveRegion.textContent = "";
          }, 3000);
          hideOverlay();
        },
      });
    }

    function stopRecording() {
      if (safetyTimeout) {
        clearTimeout(safetyTimeout);
        safetyTimeout = null;
      }
      recorder.stop();
      hideOverlay();
    }

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "voice-mic-btn";
    btn.setAttribute("aria-label", "Hold to record voice message");
    btn.appendChild(createMicSvg(false));
    controls.appendChild(btn);

    var aiBadge = document.createElement("button");
    aiBadge.type = "button";
    aiBadge.className = "voice-ai-badge";
    aiBadge.textContent = "AI";
    aiBadge.setAttribute("aria-label", "Polish current text with AI");
    controls.insertBefore(aiBadge, btn);

    function requestSuggestionFromCurrentText() {
      var rawCurrent = String(textarea.value || "");
      var normalizedCurrent = normalizeTranscriptText(rawCurrent);
      if (!normalizedCurrent) {
        hideSuggestionPanel();
        liveRegion.textContent = "Add text first";
        setTimeout(function () {
          liveRegion.textContent = "";
        }, 2000);
        return;
      }
      requestSuggestion("", normalizedCurrent, "full");
    }

    function startRecording() {
      ensureRecorder();
      recorder.start();
      showOverlay();
      safetyTimeout = setTimeout(function () {
        stopRecording();
      }, MAX_RECORDING_SECONDS * 1000);
    }

    btn.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      startRecording();
    });

    btn.addEventListener("pointerup", function (e) {
      e.preventDefault();
      stopRecording();
    });

    aiBadge.addEventListener("click", function (e) {
      e.preventDefault();
      requestSuggestionFromCurrentText();
    });

    btn.addEventListener("lostpointercapture", function () {
      if (recorder && recorder.recording) {
        stopRecording();
      }
    });

    btn.addEventListener("keydown", function (e) {
      if (e.key !== " " || e.repeat) return;
      e.preventDefault();
      if (!recorder || !recorder.recording) {
        startRecording();
      }
    });

    btn.addEventListener("keyup", function (e) {
      if (e.key !== " ") return;
      e.preventDefault();
      if (recorder && recorder.recording) {
        stopRecording();
      }
    });

    btn.addEventListener("contextmenu", function (e) {
      e.preventDefault();
    });
  }

  function initAll(root) {
    var areas = (root || document).querySelectorAll("textarea[data-voice-enabled]");
    if (areas.length === 0) return;
    injectStyles();
    for (var i = 0; i < areas.length; i++) {
      attachVoiceInput(areas[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAll();
    });
  } else {
    initAll();
  }

  document.addEventListener("htmx:afterSettle", function (event) {
    initAll(event.detail.elt);
  });
})();
