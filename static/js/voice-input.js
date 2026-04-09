/**
 * Voice-to-Text Input Module
 *
 * WhatsApp-style push-to-talk voice input for textareas.
 * Auto-attaches to any <textarea data-voice-enabled>.
 * Uses the browser-native Web Speech API — no backend required.
 */
(function () {
  "use strict";

  // ── Feature Detection ───────────────────────────────────────────────
  var SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    return; // Browser does not support Speech Recognition
  }

  // ── Task 1: Core Speech API Logic ──────────────────────────────────

  /**
   * Thin wrapper around the Web Speech API.
   * @param {Object} opts
   * @param {Function} opts.onResult  - called with final transcript string
   * @param {Function} opts.onError   - called with error event
   */
  function VoiceRecorder(opts) {
    this._recording = false;
    this._chunks = [];
    this._onResult = opts.onResult || function () {};
    this._onError = opts.onError || function () {};

    this._recognition = new SpeechRecognition();
    this._recognition.continuous = true;
    this._recognition.interimResults = true;
    this._recognition.lang =
      document.documentElement.lang || "en-US";

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
        return; // Normal on manual stop
      }
      self._recording = false;
      self._onError(event);
    };

    this._recognition.onend = function () {
      if (self._recording) {
        // Deliver collected text
        self._recording = false;
        var text = self._chunks.join(" ").trim();
        if (text) {
          self._onResult(text);
        }
      }
    };
  }

  Object.defineProperty(VoiceRecorder.prototype, "recording", {
    get: function () {
      return this._recording;
    },
  });

  VoiceRecorder.prototype.start = function () {
    this._chunks = [];
    this._recording = true;
    try {
      this._recognition.start();
    } catch (e) {
      // Already started — ignore
    }
  };

  VoiceRecorder.prototype.stop = function () {
    if (!this._recording) return;
    // _recording stays true so onend callback fires delivery
    try {
      this._recognition.stop();
    } catch (e) {
      this._recording = false;
    }
  };

  // ── Task 2: UI Elements ────────────────────────────────────────────

  var SVG_NS = "http://www.w3.org/2000/svg";

  function createMicIcon() {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("class", "w-5 h-5");
    svg.setAttribute("aria-hidden", "true");

    var path1 = document.createElementNS(SVG_NS, "path");
    path1.setAttribute(
      "d",
      "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"
    );
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

  function createActiveMicIcon() {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "currentColor");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("class", "w-6 h-6");
    svg.setAttribute("aria-hidden", "true");

    var path1 = document.createElementNS(SVG_NS, "path");
    path1.setAttribute(
      "d",
      "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"
    );
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

  // ── Inject Styles ──────────────────────────────────────────────────

  var style = document.createElement("style");
  style.textContent = [
    /* Mic button — absolute bottom-right of textarea wrapper */
    ".voice-mic-btn {",
    "  position: absolute;",
    "  bottom: 8px;",
    "  right: 8px;",
    "  width: 32px;",
    "  height: 32px;",
    "  min-width: 40px;",
    "  min-height: 40px;",
    "  display: flex;",
    "  align-items: center;",
    "  justify-content: center;",
    "  border-radius: 9999px;",
    "  background: transparent;",
    "  border: none;",
    "  color: #94a3b8;",
    "  cursor: pointer;",
    "  padding: 0;",
    "  transition: color 0.15s ease, background 0.15s ease;",
    "  touch-action: none;",
    "  user-select: none;",
    "  -webkit-user-select: none;",
    "  z-index: 10;",
    "}",
    ".voice-mic-btn:hover {",
    "  color: #475569;",
    "  background: rgba(148, 163, 184, 0.12);",
    "}",
    ".voice-mic-btn:focus-visible {",
    "  outline: 2px solid rgba(59, 130, 246, 0.5);",
    "  outline-offset: 2px;",
    "}",
    /* Dark mode */
    ".dark .voice-mic-btn {",
    "  color: #64748b;",
    "}",
    ".dark .voice-mic-btn:hover {",
    "  color: #cbd5e1;",
    "  background: rgba(100, 116, 139, 0.18);",
    "}",
    /* Recording overlay */
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
    /* Pulsing mic icon */
    ".voice-overlay-mic {",
    "  color: #ef4444;",
    "  animation: voice-pulse 1.5s ease-in-out infinite;",
    "}",
    "@keyframes voice-pulse {",
    "  0%, 100% { opacity: 1; transform: scale(1); }",
    "  50% { opacity: 0.6; transform: scale(1.12); }",
    "}",
    /* Waveform bars */
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
    ".voice-waveform > div:nth-child(1) { animation-delay: 0s;    height: 8px; }",
    ".voice-waveform > div:nth-child(2) { animation-delay: 0.1s;  height: 16px; }",
    ".voice-waveform > div:nth-child(3) { animation-delay: 0.2s;  height: 24px; }",
    ".voice-waveform > div:nth-child(4) { animation-delay: 0.3s;  height: 16px; }",
    ".voice-waveform > div:nth-child(5) { animation-delay: 0.4s;  height: 8px; }",
    "@keyframes voice-wave {",
    "  0%   { transform: scaleY(0.4); }",
    "  100% { transform: scaleY(1); }",
    "}",
    /* Timer */
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
    /* Screen-reader only utility */
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
    /* Reduced motion */
    "@media (prefers-reduced-motion: reduce) {",
    "  .voice-overlay-mic { animation: none; }",
    "  .voice-waveform > div { animation: none; }",
    "}",
  ].join("\n");
  document.head.appendChild(style);

  // ── Helpers ─────────────────────────────────────────────────────────

  function formatTime(seconds) {
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function createOverlay() {
    var overlay = document.createElement("div");
    overlay.className = "voice-overlay";

    // Mic icon
    var micWrap = document.createElement("div");
    micWrap.className = "voice-overlay-mic";
    micWrap.appendChild(createActiveMicIcon());
    overlay.appendChild(micWrap);

    // Waveform bars
    var waveform = document.createElement("div");
    waveform.className = "voice-waveform";
    for (var i = 0; i < 5; i++) {
      waveform.appendChild(document.createElement("div"));
    }
    overlay.appendChild(waveform);

    // Timer
    var timer = document.createElement("span");
    timer.className = "voice-timer";
    timer.textContent = "0:00";
    overlay.appendChild(timer);

    return overlay;
  }

  // ── Task 3: Push-to-Talk & Auto-Init ───────────────────────────────

  function attachVoiceInput(textarea) {
    if (textarea.dataset.voiceAttached === "true") return;
    textarea.dataset.voiceAttached = "true";

    // Ensure parent can position the button
    var wrapper = textarea.parentElement;
    var pos = window.getComputedStyle(wrapper).position;
    if (pos === "static" || pos === "") {
      wrapper.style.position = "relative";
    }

    // Accessibility live region
    var liveRegion = document.createElement("span");
    liveRegion.setAttribute("aria-live", "polite");
    liveRegion.className = "voice-sr-only";
    wrapper.appendChild(liveRegion);

    // Overlay state
    var overlay = null;
    var timerInterval = null;
    var elapsed = 0;

    function showOverlay() {
      elapsed = 0;
      overlay = createOverlay();
      wrapper.appendChild(overlay);
      var timerEl = overlay.querySelector(".voice-timer");
      timerInterval = setInterval(function () {
        elapsed++;
        if (timerEl) {
          timerEl.textContent = formatTime(elapsed);
        }
      }, 1000);
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

    // Voice recorder
    var recorder = new VoiceRecorder({
      onResult: function (text) {
        var current = textarea.value;
        if (current && !current.endsWith(" ")) {
          textarea.value = current + " " + text;
        } else {
          textarea.value = (current || "") + text;
        }
        // Notify Alpine / HTMX
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        liveRegion.textContent = "Voice text added";
      },
      onError: function (err) {
        console.warn("Voice input error:", err);
        hideOverlay();
      },
    });

    // Mic button
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "voice-mic-btn";
    btn.setAttribute("aria-label", "Hold to record voice message");
    btn.appendChild(createMicIcon());
    wrapper.appendChild(btn);

    // Pointer events — push-to-talk
    btn.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      recorder.start();
      showOverlay();
    });

    btn.addEventListener("pointerup", function (e) {
      e.preventDefault();
      recorder.stop();
      hideOverlay();
    });

    btn.addEventListener("pointerleave", function () {
      if (recorder.recording) {
        recorder.stop();
        hideOverlay();
      }
    });

    // Prevent context menu on long-press (mobile)
    btn.addEventListener("contextmenu", function (e) {
      e.preventDefault();
    });
  }

  // ── Auto-Init ──────────────────────────────────────────────────────

  function initAll(root) {
    var areas = (root || document).querySelectorAll(
      "textarea[data-voice-enabled]"
    );
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

  // Re-init after HTMX swaps new content
  document.addEventListener("htmx:afterSettle", function (event) {
    initAll(event.detail.elt);
  });
})();
