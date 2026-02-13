/**
 * Search Autofill Guard
 *
 * Prevents browser autofill/autocomplete from populating search inputs with
 * stale values that were never explicitly typed by the user. Runs a timed
 * normalization sequence that clears autofilled search fields unless the user
 * has actually interacted with the input or the value came from a URL param.
 */
(function () {
    "use strict";

    function isSearchInput(input) {
        if (!(input instanceof HTMLInputElement)) return false;
        var type = (input.getAttribute("type") || "text").toLowerCase();
        if (type !== "text" && type !== "search") return false;
        if (input.dataset.searchInput === "true") return true;
        var name = (input.getAttribute("name") || "").trim().toLowerCase();
        return name === "search" || name.endsWith("_search");
    }

    function hasExplicitSearchParam(input, params) {
        var name = (input.getAttribute("name") || "").trim();
        if (!name) return false;
        if (!params.has(name)) return false;
        return (params.get(name) || "").trim().length > 0;
    }

    function isTextEditIntent(event) {
        if (!event || event.type !== "keydown") return true;
        var key = String(event.key || "").toLowerCase();
        if (!key) return false;

        if (
            key === "tab" ||
            key === "shift" ||
            key === "control" ||
            key === "alt" ||
            key === "meta" ||
            key === "capslock" ||
            key === "escape" ||
            key === "arrowup" ||
            key === "arrowdown" ||
            key === "arrowleft" ||
            key === "arrowright" ||
            key === "home" ||
            key === "end" ||
            key === "pageup" ||
            key === "pagedown"
        ) {
            return false;
        }

        return true;
    }

    function isBeforeInputTextEdit(event) {
        if (!event || event.type !== "beforeinput") return false;
        if (!event.isTrusted) return false;
        var inputType = String(event.inputType || "");
        if (!inputType) return false;
        if (
            inputType === "insertText" ||
            inputType === "insertCompositionText" ||
            inputType === "deleteContentBackward" ||
            inputType === "deleteContentForward" ||
            inputType === "deleteByCut"
        ) {
            return true;
        }
        return false;
    }

    function markSearchInputIntent(input, event) {
        if (!(input instanceof HTMLInputElement)) return;
        if (event && event.type === "beforeinput") {
            if (!isBeforeInputTextEdit(event)) return;
            input.dataset.searchInteracted = "1";
            return;
        }
        if (!isTextEditIntent(event)) return;
        input.dataset.searchInteracted = "1";
    }

    function shouldClearAutofillValue(input, params) {
        if (!isSearchInput(input)) return false;
        if (hasExplicitSearchParam(input, params)) return false;
        if (input.dataset.searchInteracted === "1") return false;
        if (!input.value || !input.value.trim()) return false;
        return true;
    }

    function clearSearchInput(input) {
        if (!(input instanceof HTMLInputElement)) return;
        input.value = "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function clearIfAutofilled(input) {
        var params = new URLSearchParams(window.location.search || "");
        if (shouldClearAutofillValue(input, params)) {
            clearSearchInput(input);
        }
    }

    function unlockSearchInput(input) {
        if (!(input instanceof HTMLInputElement)) return;
        if (input.readOnly) {
            input.readOnly = false;
        }
    }

    function normalizeSearchInputs() {
        var params = new URLSearchParams(window.location.search || "");
        var inputs = Array.from(document.querySelectorAll("input[type='text'], input[type='search']"));
        for (var i = 0; i < inputs.length; i++) {
            var input = inputs[i];
            if (!shouldClearAutofillValue(input, params)) continue;
            clearSearchInput(input);
        }
    }

    function attachInteractionListeners() {
        var inputs = Array.from(document.querySelectorAll("input[type='text'], input[type='search']"));
        for (var i = 0; i < inputs.length; i++) {
            var input = inputs[i];
            if (!isSearchInput(input)) continue;
            if (input.dataset.searchIntentBound === "1") continue;
            input.dataset.searchIntentBound = "1";
            input.setAttribute("autocomplete", "new-password");
            input.readOnly = true;
            input.addEventListener("keydown", markSearchInputIntent.bind(null, input));
            input.addEventListener("paste", markSearchInputIntent.bind(null, input));
            input.addEventListener("drop", markSearchInputIntent.bind(null, input));
            input.addEventListener("beforeinput", markSearchInputIntent.bind(null, input));
            input.addEventListener("focus", function () {
                unlockSearchInput(input);
                clearIfAutofilled(input);
            });
            input.addEventListener("pointerdown", unlockSearchInput.bind(null, input));
            input.addEventListener("touchstart", unlockSearchInput.bind(null, input), { passive: true });
        }
    }

    function runNormalizationSequence() {
        attachInteractionListeners();
        normalizeSearchInputs();
        var delays = [150, 500, 1200, 2500];
        for (var i = 0; i < delays.length; i++) {
            window.setTimeout(function () {
                attachInteractionListeners();
                normalizeSearchInputs();
            }, delays[i]);
        }
    }

    document.addEventListener("DOMContentLoaded", runNormalizationSequence);
    window.addEventListener("pageshow", runNormalizationSequence);
    window.addEventListener("load", runNormalizationSequence);
    document.body.addEventListener("htmx:afterSwap", runNormalizationSequence);
})();
