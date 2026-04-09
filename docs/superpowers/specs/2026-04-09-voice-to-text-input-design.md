# Voice-to-Text Input for Tickets, Work Orders & Inbox

**Date**: 2026-04-09
**Status**: Approved

## Overview

Add WhatsApp-style push-to-talk voice input to text areas across the CRM, enabling field technicians and support agents to dictate updates instead of typing. Uses the browser-native Web Speech API — no server-side component or external API required.

## Integration Points

| Area | Template | Textarea |
|------|----------|----------|
| Ticket comments | `templates/admin/tickets/detail.html` (~line 268) | Comment body textarea |
| Work order notes | `templates/admin/operations/work_order_detail.html` | Notes textarea |
| Inbox replies | `templates/admin/crm/_message_thread.html` (~line 983) | `#reply-textarea` |
| Inbox new conversation | `templates/admin/crm/inbox.html` (~line 849) | Compose textarea |

## Architecture

### Single JS Module: `static/js/voice-input.js`

A standalone module that auto-attaches to any textarea with `data-voice-enabled`. No Alpine.js dependency, no backend changes.

**Initialization:**
- On `DOMContentLoaded`, scans for `<textarea data-voice-enabled>`
- Also observes DOM mutations (for HTMX-loaded partials that may contain voice-enabled textareas)
- If `window.SpeechRecognition || window.webkitSpeechRecognition` is unsupported, the mic button is not rendered — no error, no broken UI

**Template opt-in:**
```html
<div class="relative"> <!-- wrapper for mic positioning -->
    <textarea data-voice-enabled name="body" rows="3">...</textarea>
</div>
```

**Script inclusion:**
- Added to `layouts/admin.html` so it's globally available
- Any future textarea can opt in with just the `data-voice-enabled` attribute

## Interaction: Push-to-Talk

- `pointerdown` on mic button → start speech recognition, show recording overlay
- `pointerup` or `pointerleave` → stop recognition, append final transcript to textarea, hide overlay
- Works for both mouse (desktop) and touch (mobile/field devices)
- Transcribed text is appended to existing textarea content (separated by a space if not empty)
- After insertion, dispatches an `input` event so Alpine.js bindings and auto-resize behaviors stay in sync

## UI States

### Mic Button (Idle)

- Size: `w-8 h-8`, positioned absolute bottom-right inside the textarea wrapper
- Color: `text-slate-400 dark:text-slate-500`
- Hover: `text-slate-600 dark:text-slate-300`
- Touch target: 40px minimum (WCAG compliance)
- Cursor: pointer

### Recording Overlay (Active)

Overlays the textarea while recording:

- Background: `bg-red-50 dark:bg-red-900/20`, border: `border-red-300 dark:border-red-700`
- Left: Pulsing red mic icon (`animate-pulse text-red-500`)
- Center: CSS waveform bars (4-5 bars with staggered `animation-delay`, `bg-red-400`)
- Right: Elapsed timer in `font-display text-red-600 dark:text-red-400` (format: `0:00`)
- Textarea remains underneath — overlay removed on pointer release

### After Recording

- Overlay disappears, textarea shows with appended transcribed text
- Textarea auto-resizes if it has auto-grow behavior (inbox textareas do)

## Browser Support

- Chrome, Edge, Samsung Internet, Opera: Full support via `webkitSpeechRecognition`
- Safari: Partial support (may work on newer versions)
- Firefox: Not supported — mic button will not render
- Graceful degradation: no mic button shown, no errors, no broken UI

## Accessibility

- Mic button: `aria-label="Hold to record voice message"`
- During recording: `aria-live="polite"` region announces "Recording..."
- On completion: screen reader announces "Voice text added"
- Recording overlay respects `prefers-reduced-motion: reduce` (disables waveform animation, keeps static indicator)

## Dark Mode

All UI elements have paired light/dark variants:

| Element | Light | Dark |
|---------|-------|------|
| Mic button | `text-slate-400` | `dark:text-slate-500` |
| Mic hover | `text-slate-600` | `dark:text-slate-300` |
| Overlay bg | `bg-red-50` | `dark:bg-red-900/20` |
| Overlay border | `border-red-300` | `dark:border-red-700` |
| Timer text | `text-red-600` | `dark:text-red-400` |
| Waveform bars | `bg-red-400` | `dark:bg-red-400` |

## Files to Create

| File | Purpose |
|------|---------|
| `static/js/voice-input.js` | Voice input module (Speech API, UI, push-to-talk logic) |

## Files to Modify

| File | Change |
|------|--------|
| `templates/layouts/admin.html` | Add `<script src="/static/js/voice-input.js"></script>` |
| `templates/admin/tickets/detail.html` | Add `data-voice-enabled` + wrapper div on comment textarea |
| `templates/admin/operations/work_order_detail.html` | Add `data-voice-enabled` + wrapper div on notes textarea |
| `templates/admin/crm/_message_thread.html` | Add `data-voice-enabled` + wrapper div on reply textarea |
| `templates/admin/crm/inbox.html` | Add `data-voice-enabled` + wrapper div on compose textarea |

## No Backend Changes

The voice module writes transcribed text directly into the textarea. Existing form submissions handle it from there. The server never receives audio — all transcription happens in the browser.

## Testing

- **Manual**: Test push-to-talk in Chrome on desktop and mobile (Android Chrome)
- **Unit**: Verify graceful degradation when `SpeechRecognition` is undefined
- **E2E (Playwright)**: Cannot test actual speech recognition (no mic in headless browser), but can verify mic button renders on voice-enabled textareas and doesn't render on others
