# Voice-to-Text Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add WhatsApp-style push-to-talk voice input to ticket comments, work order notes, and inbox message textareas using the browser-native Web Speech API.

**Architecture:** A single standalone JS module (`static/js/voice-input.js`) auto-attaches to any textarea with `data-voice-enabled`. It injects a mic button, handles push-to-talk interaction, shows a recording overlay with timer and waveform, and appends transcribed text. No backend changes needed.

**Tech Stack:** Web Speech API (`webkitSpeechRecognition`), vanilla JS, Tailwind CSS classes

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `static/js/voice-input.js` | Create | Voice input module: Speech API, mic button injection, recording overlay, push-to-talk logic |
| `templates/layouts/admin.html` | Modify | Add script include for voice-input.js |
| `templates/admin/tickets/detail.html` | Modify | Add `data-voice-enabled` to comment textarea |
| `templates/admin/crm/_message_thread.html` | Modify | Add `data-voice-enabled` to reply textarea |
| `templates/admin/crm/inbox.html` | Modify | Add `data-voice-enabled` to new conversation compose textarea |

> **Note:** `templates/admin/operations/work_order_detail.html` currently has no notes textarea form. Skip work order integration for now — it can be added when that form exists.

---

### Task 1: Create the Voice Input JS Module — Core Speech API Logic

**Files:**
- Create: `static/js/voice-input.js`

This task builds the core speech recognition wrapper with no UI yet. The module exports an internal class `VoiceRecorder` that manages the Web Speech API lifecycle.

- [ ] **Step 1: Create `static/js/voice-input.js` with feature detection and VoiceRecorder class**

```javascript
/**
 * Voice-to-Text Input Module
 *
 * Auto-attaches WhatsApp-style push-to-talk voice input to any
 * textarea with the `data-voice-enabled` attribute.
 *
 * Browser support: Chrome, Edge, Samsung Internet, Opera.
 * Gracefully hidden when SpeechRecognition is unavailable.
 */
(function () {
    "use strict";

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return; // Graceful degradation — no mic button rendered

    /**
     * Manages a single SpeechRecognition session.
     * Collects interim + final results, invokes callback with full transcript on stop.
     */
    class VoiceRecorder {
        constructor(onResult, onError) {
            this._onResult = onResult;
            this._onError = onError;
            this._recognition = null;
            this._transcript = "";
            this._recording = false;
        }

        get recording() {
            return this._recording;
        }

        start() {
            if (this._recording) return;
            this._transcript = "";
            this._recognition = new SpeechRecognition();
            this._recognition.continuous = true;
            this._recognition.interimResults = true;
            this._recognition.lang = document.documentElement.lang || "en-US";

            this._recognition.onresult = (event) => {
                let final = "";
                for (let i = 0; i < event.results.length; i++) {
                    const result = event.results[i];
                    if (result.isFinal) {
                        final += result[0].transcript;
                    }
                }
                this._transcript = final;
            };

            this._recognition.onerror = (event) => {
                if (event.error === "aborted") return; // Normal on stop()
                this._recording = false;
                this._onError(event.error);
            };

            this._recognition.onend = () => {
                this._recording = false;
            };

            this._recognition.start();
            this._recording = true;
        }

        stop() {
            if (!this._recording || !this._recognition) return "";
            this._recognition.stop();
            this._recording = false;
            const text = this._transcript.trim();
            this._onResult(text);
            return text;
        }
    }

    // -- Remaining tasks will add UI injection and initialization here --

})();
```

- [ ] **Step 2: Verify file loads without errors**

Open any admin page in Chrome DevTools console and confirm no JS errors from voice-input.js. (The script is not yet included in the layout — manually test by pasting into console.)

- [ ] **Step 3: Commit**

```bash
git add static/js/voice-input.js
git commit -m "feat: add voice-input.js with core Speech API wrapper"
```

---

### Task 2: Add Recording Overlay UI and Mic Button Injection

**Files:**
- Modify: `static/js/voice-input.js`

This task adds the visual elements: mic button injection into textareas, recording overlay with timer and waveform animation. All DOM elements are built with safe `document.createElement` calls — no innerHTML.

- [ ] **Step 1: Add mic SVG icon creation functions and CSS injection**

Add after the `VoiceRecorder` class in `voice-input.js` (replace the `// -- Remaining tasks` comment):

```javascript
    // ── SVG Icon Builders (safe DOM creation, no innerHTML) ────
    function createMicIcon() {
        const NS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(NS, "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "none");
        svg.setAttribute("stroke", "currentColor");
        svg.setAttribute("stroke-width", "2");
        svg.setAttribute("stroke-linecap", "round");
        svg.setAttribute("stroke-linejoin", "round");
        svg.setAttribute("class", "w-5 h-5");
        svg.setAttribute("aria-hidden", "true");

        const path1 = document.createElementNS(NS, "path");
        path1.setAttribute("d", "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z");
        const path2 = document.createElementNS(NS, "path");
        path2.setAttribute("d", "M19 10v2a7 7 0 0 1-14 0v-2");
        const line = document.createElementNS(NS, "line");
        line.setAttribute("x1", "12"); line.setAttribute("x2", "12");
        line.setAttribute("y1", "19"); line.setAttribute("y2", "22");

        svg.appendChild(path1);
        svg.appendChild(path2);
        svg.appendChild(line);
        return svg;
    }

    function createActiveMicIcon() {
        const NS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(NS, "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "currentColor");
        svg.setAttribute("class", "w-6 h-6");
        svg.setAttribute("aria-hidden", "true");

        const path1 = document.createElementNS(NS, "path");
        path1.setAttribute("d", "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z");
        const path2 = document.createElementNS(NS, "path");
        path2.setAttribute("d", "M19 10v2a7 7 0 0 1-14 0v-2");
        path2.setAttribute("fill", "none");
        path2.setAttribute("stroke", "currentColor");
        path2.setAttribute("stroke-width", "2");
        const line = document.createElementNS(NS, "line");
        line.setAttribute("x1", "12"); line.setAttribute("x2", "12");
        line.setAttribute("y1", "19"); line.setAttribute("y2", "22");
        line.setAttribute("stroke", "currentColor");
        line.setAttribute("stroke-width", "2");

        svg.appendChild(path1);
        svg.appendChild(path2);
        svg.appendChild(line);
        return svg;
    }

    // ── Injected Styles ────────────────────────────────────────
    const STYLE_ID = "voice-input-styles";
    if (!document.getElementById(STYLE_ID)) {
        const style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = [
            ".voice-mic-btn {",
            "  position: absolute; bottom: 8px; right: 8px;",
            "  width: 32px; height: 32px; min-width: 40px; min-height: 40px;",
            "  display: flex; align-items: center; justify-content: center;",
            "  border-radius: 9999px; border: none; background: transparent;",
            "  cursor: pointer; color: rgb(148 163 184); z-index: 10; padding: 0;",
            "  transition: color 0.15s, background-color 0.15s;",
            "  -webkit-touch-callout: none; -webkit-user-select: none; user-select: none;",
            "}",
            ".voice-mic-btn:hover {",
            "  color: rgb(71 85 105); background: rgb(241 245 249);",
            "}",
            ".dark .voice-mic-btn { color: rgb(100 116 139); }",
            ".dark .voice-mic-btn:hover {",
            "  color: rgb(203 213 225); background: rgb(30 41 59);",
            "}",
            "",
            ".voice-overlay {",
            "  position: absolute; inset: 0; display: flex;",
            "  align-items: center; justify-content: center; gap: 16px;",
            "  border-radius: inherit; z-index: 20; pointer-events: none;",
            "  background: rgb(254 242 242); border: 2px solid rgb(252 165 165);",
            "}",
            ".dark .voice-overlay {",
            "  background: rgba(127, 29, 29, 0.2); border-color: rgb(185 28 28);",
            "}",
            "",
            ".voice-overlay-mic {",
            "  color: rgb(239 68 68);",
            "  animation: voice-pulse 1.5s ease-in-out infinite;",
            "}",
            "@media (prefers-reduced-motion: reduce) {",
            "  .voice-overlay-mic { animation: none; }",
            "}",
            "@keyframes voice-pulse {",
            "  0%, 100% { opacity: 1; transform: scale(1); }",
            "  50% { opacity: 0.6; transform: scale(1.1); }",
            "}",
            "",
            ".voice-waveform {",
            "  display: flex; align-items: center; gap: 3px; height: 24px;",
            "}",
            ".voice-waveform-bar {",
            "  width: 3px; border-radius: 9999px; background: rgb(248 113 113);",
            "  animation: voice-wave 0.8s ease-in-out infinite alternate;",
            "}",
            ".voice-waveform-bar:nth-child(1) { animation-delay: 0s; }",
            ".voice-waveform-bar:nth-child(2) { animation-delay: 0.15s; }",
            ".voice-waveform-bar:nth-child(3) { animation-delay: 0.3s; }",
            ".voice-waveform-bar:nth-child(4) { animation-delay: 0.45s; }",
            ".voice-waveform-bar:nth-child(5) { animation-delay: 0.1s; }",
            "@keyframes voice-wave {",
            "  0% { height: 6px; } 100% { height: 22px; }",
            "}",
            "@media (prefers-reduced-motion: reduce) {",
            "  .voice-waveform-bar { animation: none; height: 12px; }",
            "}",
            "",
            ".voice-timer {",
            "  font-family: 'Outfit', sans-serif; font-size: 0.875rem;",
            "  font-weight: 600; font-variant-numeric: tabular-nums;",
            "  color: rgb(220 38 38);",
            "}",
            ".dark .voice-timer { color: rgb(248 113 113); }",
            "",
            ".voice-sr-only {",
            "  position: absolute; width: 1px; height: 1px; padding: 0;",
            "  margin: -1px; overflow: hidden; clip: rect(0,0,0,0); border: 0;",
            "}"
        ].join("\n");
        document.head.appendChild(style);
    }
```

- [ ] **Step 2: Add overlay creation and timer helpers using safe DOM methods**

Add after the styles block:

```javascript
    // ── Helpers ─────────────────────────────────────────────────
    function formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return m + ":" + (s < 10 ? "0" : "") + s;
    }

    function createOverlay() {
        const overlay = document.createElement("div");
        overlay.className = "voice-overlay";

        const micWrap = document.createElement("div");
        micWrap.className = "voice-overlay-mic";
        micWrap.appendChild(createActiveMicIcon());
        overlay.appendChild(micWrap);

        const waveform = document.createElement("div");
        waveform.className = "voice-waveform";
        for (let i = 0; i < 5; i++) {
            const bar = document.createElement("div");
            bar.className = "voice-waveform-bar";
            waveform.appendChild(bar);
        }
        overlay.appendChild(waveform);

        const timer = document.createElement("span");
        timer.className = "voice-timer";
        timer.textContent = "0:00";
        overlay.appendChild(timer);

        return overlay;
    }
```

- [ ] **Step 3: Commit**

```bash
git add static/js/voice-input.js
git commit -m "feat: add voice-input UI styles, icons, and overlay helpers"
```

---

### Task 3: Wire Up Push-to-Talk Interaction and Auto-Initialization

**Files:**
- Modify: `static/js/voice-input.js`

This task connects the VoiceRecorder to the UI elements, handles pointer events for push-to-talk, and auto-initializes on page load and HTMX swaps.

- [ ] **Step 1: Add the `attachVoiceInput` function**

Add after `createOverlay()`:

```javascript
    // ── Attach Voice Input to a Textarea ───────────────────────
    function attachVoiceInput(textarea) {
        if (textarea.dataset.voiceAttached === "true") return;
        textarea.dataset.voiceAttached = "true";

        // Ensure the parent is positioned for absolute mic button
        const wrapper = textarea.parentElement;
        if (wrapper && getComputedStyle(wrapper).position === "static") {
            wrapper.style.position = "relative";
        }

        // Accessibility live region
        const liveRegion = document.createElement("span");
        liveRegion.className = "voice-sr-only";
        liveRegion.setAttribute("aria-live", "polite");
        wrapper.appendChild(liveRegion);

        // Mic button
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "voice-mic-btn";
        btn.setAttribute("aria-label", "Hold to record voice message");
        btn.appendChild(createMicIcon());
        wrapper.appendChild(btn);

        var overlay = null;
        var timerInterval = null;
        var seconds = 0;

        var recorder = new VoiceRecorder(
            function onResult(text) {
                if (!text) return;
                var current = textarea.value;
                var separator = current && !current.endsWith(" ") ? " " : "";
                textarea.value = current + separator + text;
                // Fire input event for Alpine.js bindings and auto-resize
                textarea.dispatchEvent(new Event("input", { bubbles: true }));
                liveRegion.textContent = "Voice text added";
                setTimeout(function () { liveRegion.textContent = ""; }, 2000);
            },
            function onError(error) {
                console.warn("Voice input error:", error);
                hideOverlay();
            }
        );

        function showOverlay() {
            if (overlay) return;
            overlay = createOverlay();
            wrapper.appendChild(overlay);
            seconds = 0;
            timerInterval = setInterval(function () {
                seconds++;
                var timerEl = overlay.querySelector(".voice-timer");
                if (timerEl) timerEl.textContent = formatTime(seconds);
            }, 1000);
            liveRegion.textContent = "Recording";
        }

        function hideOverlay() {
            if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
            if (overlay) { overlay.remove(); overlay = null; }
        }

        // Push-to-talk: pointer down = start, pointer up = stop
        btn.addEventListener("pointerdown", function (e) {
            e.preventDefault();
            btn.setPointerCapture(e.pointerId);
            recorder.start();
            showOverlay();
        });

        btn.addEventListener("pointerup", function () {
            recorder.stop();
            hideOverlay();
        });

        btn.addEventListener("pointerleave", function () {
            if (recorder.recording) {
                recorder.stop();
                hideOverlay();
            }
        });

        // Prevent context menu on long press (mobile)
        btn.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    }
```

- [ ] **Step 2: Add auto-initialization on load and HTMX swaps**

Add after `attachVoiceInput()`:

```javascript
    // ── Auto-Initialize ────────────────────────────────────────
    function initAll(root) {
        var target = root || document;
        target.querySelectorAll("textarea[data-voice-enabled]").forEach(attachVoiceInput);
    }

    // Initialize on DOM ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { initAll(); });
    } else {
        initAll();
    }

    // Re-initialize after HTMX swaps (for dynamically loaded partials)
    document.body.addEventListener("htmx:afterSettle", function (event) {
        initAll(event.detail.elt);
    });
```

- [ ] **Step 3: Commit**

```bash
git add static/js/voice-input.js
git commit -m "feat: wire up push-to-talk interaction and auto-init for voice input"
```

---

### Task 4: Include voice-input.js in Admin Layout

**Files:**
- Modify: `templates/layouts/admin.html:1417`

- [ ] **Step 1: Add script tag before the closing block**

Find this line in `templates/layouts/admin.html` (line 1417):
```html
    </script>
{% include "components/modals/confirm_modal.html" %}
{% endblock %}
```

Add the voice-input script between the closing `</script>` and the modal include:

```html
    </script>
    <script src="{{ url_for('static', path='js/voice-input.js') }}"></script>
{% include "components/modals/confirm_modal.html" %}
{% endblock %}
```

- [ ] **Step 2: Verify script loads on any admin page**

Open Chrome DevTools Network tab, navigate to any admin page, confirm `voice-input.js` loads with 200 status.

- [ ] **Step 3: Commit**

```bash
git add templates/layouts/admin.html
git commit -m "feat: include voice-input.js in admin layout"
```

---

### Task 5: Add Voice Input to Ticket Comment Textarea

**Files:**
- Modify: `templates/admin/tickets/detail.html:268-273`

- [ ] **Step 1: Add `data-voice-enabled` attribute to the comment textarea**

Find this code in `templates/admin/tickets/detail.html` (line 268):
```html
                <textarea name="body"
                           rows="3"
                           required
                           data-mention-textarea
                           class="block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 placeholder-slate-400 transition-all focus:border-amber-500 focus:outline-none focus:ring-2 focus:ring-amber-500/20 dark:border-slate-600 dark:bg-slate-700 dark:text-white dark:placeholder-slate-500 dark:focus:border-amber-500"
                           placeholder="Type your comment here..."></textarea>
```

Change to (add `data-voice-enabled` and add right padding for mic button):
```html
                <textarea name="body"
                           rows="3"
                           required
                           data-mention-textarea
                           data-voice-enabled
                           class="block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 pr-12 text-sm text-slate-900 placeholder-slate-400 transition-all focus:border-amber-500 focus:outline-none focus:ring-2 focus:ring-amber-500/20 dark:border-slate-600 dark:bg-slate-700 dark:text-white dark:placeholder-slate-500 dark:focus:border-amber-500"
                           placeholder="Type your comment here..."></textarea>
```

Changes: added `data-voice-enabled` attribute and changed `px-4` padding to include `pr-12` so text doesn't overlap the mic button.

- [ ] **Step 2: Verify mic button appears on ticket detail page**

Navigate to any ticket detail page. Confirm:
- Mic button visible bottom-right of comment textarea
- Hovering changes color
- No layout breakage

- [ ] **Step 3: Test push-to-talk**

Hold the mic button, speak a sentence, release. Confirm:
- Recording overlay appears with red mic, waveform, timer
- On release, transcribed text appears in textarea
- Text appends if textarea already has content

- [ ] **Step 4: Commit**

```bash
git add templates/admin/tickets/detail.html
git commit -m "feat: enable voice input on ticket comment textarea"
```

---

### Task 6: Add Voice Input to Inbox Reply Textarea

**Files:**
- Modify: `templates/admin/crm/_message_thread.html:983-1010`

- [ ] **Step 1: Add `data-voice-enabled` to the reply textarea**

Find this code in `templates/admin/crm/_message_thread.html` (line 983):
```html
                <textarea x-ref="messageInput"
                           id="reply-textarea"
                           data-channel-type="{{ conversation.channel }}"
                           name="message"
                           rows="1"
                           placeholder="Type your message..."
                           class="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-sm text-slate-900 placeholder-slate-400 resize-none focus:outline-none focus:border-primary-500/50 focus:ring-1 focus:ring-primary-500/30 transition-all dark:bg-slate-800/50 dark:border-slate-700/50 dark:text-white dark:placeholder-slate-500"
```

Change to (add `data-voice-enabled` and `pr-12`):
```html
                <textarea x-ref="messageInput"
                           id="reply-textarea"
                           data-channel-type="{{ conversation.channel }}"
                           data-voice-enabled
                           name="message"
                           rows="1"
                           placeholder="Type your message..."
                           class="w-full px-4 py-3 pr-12 bg-slate-50 border border-slate-200 rounded-2xl text-sm text-slate-900 placeholder-slate-400 resize-none focus:outline-none focus:border-primary-500/50 focus:ring-1 focus:ring-primary-500/30 transition-all dark:bg-slate-800/50 dark:border-slate-700/50 dark:text-white dark:placeholder-slate-500"
```

Changes: added `data-voice-enabled` and `pr-12` for mic button spacing.

- [ ] **Step 2: Verify on inbox conversation page**

Open an inbox conversation. Confirm mic button appears in the reply textarea without breaking the existing layout, mentions, or auto-resize behavior.

- [ ] **Step 3: Test that `input` event fires correctly**

Hold mic, speak, release. Confirm:
- Transcribed text appears in textarea
- Textarea auto-resizes (the existing `@input` handler triggers)
- Draft storage and typing indicator still work (they listen on `input` event)

- [ ] **Step 4: Commit**

```bash
git add templates/admin/crm/_message_thread.html
git commit -m "feat: enable voice input on inbox reply textarea"
```

---

### Task 7: Add Voice Input to Inbox New Conversation Textarea

**Files:**
- Modify: `templates/admin/crm/inbox.html:849-856`

- [ ] **Step 1: Add `data-voice-enabled` and wrapper positioning to compose textarea**

Find this code in `templates/admin/crm/inbox.html` (line 849):
```html
                <div>
                    <label class="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Message</label>
                    <textarea name="message"
                               x-ref="newConversationMessage"
                               rows="4"
                               placeholder="Write your message..."
                               :readonly="newConversationChannel === 'whatsapp' && selectedWhatsappTemplateName"
                               class="block w-full rounded-xl border border-slate-200 bg-slate-50/50 px-4 py-3 text-sm text-slate-900 placeholder-slate-400 transition-all focus:border-amber-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-amber-500/20 dark:border-slate-600 dark:bg-slate-700/50 dark:text-white dark:placeholder-slate-500 dark:focus:border-amber-500"></textarea>
                </div>
```

Change to (add `relative` to wrapper div, `data-voice-enabled` and `pr-12` to textarea):
```html
                <div class="relative">
                    <label class="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Message</label>
                    <textarea name="message"
                               x-ref="newConversationMessage"
                               rows="4"
                               data-voice-enabled
                               placeholder="Write your message..."
                               :readonly="newConversationChannel === 'whatsapp' && selectedWhatsappTemplateName"
                               class="block w-full rounded-xl border border-slate-200 bg-slate-50/50 px-4 py-3 pr-12 text-sm text-slate-900 placeholder-slate-400 transition-all focus:border-amber-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-amber-500/20 dark:border-slate-600 dark:bg-slate-700/50 dark:text-white dark:placeholder-slate-500 dark:focus:border-amber-500"></textarea>
                </div>
```

Changes: added `class="relative"` to parent div, added `data-voice-enabled` and `pr-12` to textarea.

- [ ] **Step 2: Verify on inbox page**

Click "New Conversation" in inbox. Confirm mic button appears in the message compose textarea. Verify it doesn't appear when WhatsApp template is selected and textarea is readonly.

- [ ] **Step 3: Commit**

```bash
git add templates/admin/crm/inbox.html
git commit -m "feat: enable voice input on inbox new conversation textarea"
```

---

### Task 8: Dark Mode Verification and Polish

**Files:**
- Modify: `static/js/voice-input.js` (if fixes needed)

- [ ] **Step 1: Verify dark mode strategy**

Check the `<html>` tag in the rendered page. If it uses a `.dark` class (Tailwind class strategy), the CSS in voice-input.js already uses `.dark` selectors — no changes needed.

If the app uses `@media (prefers-color-scheme: dark)` instead, update the `.dark` selectors in the style block to use the media query.

- [ ] **Step 2: Test dark mode on all three integration points**

Toggle dark mode. Visit:
1. Ticket detail page — check mic button and recording overlay colors
2. Inbox conversation — check mic button and recording overlay colors
3. Inbox new conversation — check mic button and recording overlay colors

Verify:
- Mic button is visible against dark backgrounds
- Recording overlay uses dark variants (dark red bg, lighter border)
- Timer text is readable

- [ ] **Step 3: Commit (if changes were needed)**

```bash
git add static/js/voice-input.js
git commit -m "fix: adjust dark mode strategy for voice input styles"
```

---

### Task 9: Final Integration Test

- [ ] **Step 1: Full test pass on all three textareas**

Test each voice-enabled textarea in Chrome:

| Page | Test | Expected |
|------|------|----------|
| Ticket detail | Hold mic, speak, release | Text appended to comment textarea |
| Ticket detail | Type text, then voice | Voice text appended after existing text with space |
| Inbox reply | Hold mic, speak, release | Text appended, textarea auto-resizes |
| Inbox reply | Submit form after voice | Message sends normally |
| Inbox new conversation | Hold mic, speak, release | Text appended to compose textarea |
| Any page in Firefox | Check for mic button | Mic button should NOT render |

- [ ] **Step 2: Test mobile touch interaction**

Open Chrome DevTools device toolbar (mobile mode). On each textarea:
- Touch and hold mic button — recording starts
- Release — text appears
- Long-press does NOT trigger context menu

- [ ] **Step 3: Test accessibility**

With screen reader or by inspecting DOM:
- Mic button has `aria-label="Hold to record voice message"`
- During recording, live region announces "Recording"
- After recording, live region announces "Voice text added"

- [ ] **Step 4: Final commit if any adjustments were made**

```bash
git add -A
git commit -m "feat: complete voice-to-text input for tickets and inbox"
```
