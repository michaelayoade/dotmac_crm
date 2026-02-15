(function () {
  const SEARCH_DELAY_MS = 300;
  const searchTimers = new WeakMap();

  function isGetForm(form) {
    if (!form) return false;
    return (form.getAttribute("method") || "get").toLowerCase() === "get";
  }

  function hasHtmxBinding(el) {
    if (!el) return false;
    return (
      el.hasAttribute("hx-get") ||
      el.hasAttribute("hx-post") ||
      el.hasAttribute("hx-put") ||
      el.hasAttribute("hx-patch") ||
      el.hasAttribute("hx-delete") ||
      el.hasAttribute("hx-trigger")
    );
  }

  function shouldHandle(el, form) {
    if (!form || !isGetForm(form)) return false;
    if (form.getAttribute("data-live-submit") === "false") return false;
    if (hasHtmxBinding(el) || hasHtmxBinding(form)) return false;
    if (el.disabled) return false;
    return true;
  }

  function submitForm(form) {
    if (!form) return;
    const pageInput = form.querySelector('input[name="page"]');
    if (pageInput) pageInput.value = "1";
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return;
    }
    form.submit();
  }

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    if (target.getAttribute("data-live-filter") !== "true") return;
    const form = target.closest("form");
    if (!shouldHandle(target, form)) return;
    submitForm(form);
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const isLiveSearch =
      target.getAttribute("data-live-search") === "true" ||
      (target.name === "search" && target.getAttribute("data-live-search") !== "false");
    if (!isLiveSearch) return;

    const form = target.closest("form");
    if (!shouldHandle(target, form)) return;

    const previous = searchTimers.get(target);
    if (previous) window.clearTimeout(previous);

    const timer = window.setTimeout(() => {
      submitForm(form);
    }, SEARCH_DELAY_MS);
    searchTimers.set(target, timer);
  });
})();
