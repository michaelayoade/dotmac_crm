(function () {
  function hasValue(value) {
    return typeof value === "string" && value.trim() !== "";
  }

  function readSavedJson(storageKey) {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (_error) {
      return null;
    }
  }

  function writeSavedJson(storageKey, value) {
    window.localStorage.setItem(storageKey, JSON.stringify(value));
  }

  function getQueryParams() {
    return new URLSearchParams(window.location.search || "");
  }

  function getPathWithParams(params) {
    const qs = params.toString();
    return window.location.pathname + (qs ? "?" + qs : "");
  }

  function normalizeColumnState(columns, validKeys, defaultVisible, requiredKeys) {
    if (!Array.isArray(columns)) return defaultVisible.slice();
    const cleaned = columns.filter((key) => typeof key === "string" && validKeys.has(key));
    const base = cleaned.length ? cleaned : defaultVisible.slice();
    return Array.from(new Set(base.concat(requiredKeys || [])));
  }

  function initColumnState(config) {
    const optionSelector = config.optionSelector;
    const storageKey = config.storageKey;
    const cellAttr = config.cellAttr;
    const validKeys = new Set(config.validKeys || []);
    const defaultVisible = (config.defaultVisible || []).slice();
    const requiredKeys = (config.requiredKeys || []).slice();
    const minSelected = Math.max(Number(config.minSelected || 1), 1);
    const optionInputs = Array.from(document.querySelectorAll(optionSelector));

    if (!optionInputs.length || !storageKey || !cellAttr || !validKeys.size || !defaultVisible.length) {
      return;
    }

    function getVisibleColumns() {
      const saved = readSavedJson(storageKey);
      return normalizeColumnState(saved, validKeys, defaultVisible, requiredKeys);
    }

    function saveVisibleColumns(columns) {
      writeSavedJson(storageKey, Array.from(columns));
    }

    function applyVisibleColumns() {
      const visible = new Set(getVisibleColumns());
      validKeys.forEach((key) => {
        const cells = document.querySelectorAll("[" + cellAttr + '="' + key + '"]');
        cells.forEach((cell) => {
          cell.classList.toggle("hidden", !visible.has(key));
        });
      });
    }

    function syncOptionInputs() {
      const visible = new Set(getVisibleColumns());
      optionInputs.forEach((input) => {
        input.checked = visible.has(input.value);
      });
    }

    optionInputs.forEach((input) => {
      input.addEventListener("change", () => {
        const current = new Set(getVisibleColumns());
        if (input.checked) {
          current.add(input.value);
        } else {
          current.delete(input.value);
        }

        const withRequired = new Set(Array.from(current).concat(requiredKeys));
        if (withRequired.size < minSelected) {
          input.checked = true;
          withRequired.add(input.value);
        }

        saveVisibleColumns(withRequired);
        syncOptionInputs();
        applyVisibleColumns();
      });
    });

    if (config.toggleButtonId && config.panelId) {
      const toggle = document.getElementById(config.toggleButtonId);
      const panel = document.getElementById(config.panelId);
      const container = config.containerId ? document.getElementById(config.containerId) : null;

      if (toggle && panel) {
        toggle.addEventListener("click", () => {
          panel.classList.toggle("hidden");
        });

        document.addEventListener("click", (event) => {
          if (panel.classList.contains("hidden")) return;
          const root = container || panel.parentElement;
          if (!root) return;
          if (!root.contains(event.target)) {
            panel.classList.add("hidden");
          }
        });
      }
    }

    if (config.afterSwapTargetId) {
      document.body.addEventListener("htmx:afterSwap", (event) => {
        if (event.target && event.target.id === config.afterSwapTargetId) {
          applyVisibleColumns();
        }
      });
    }

    syncOptionInputs();
    applyVisibleColumns();
  }

  function initFilterState(config) {
    const form = document.getElementById(config.formId);
    if (!form) return;

    const fields = Array.isArray(config.fields) ? config.fields : [];
    const storageKey = config.filterStorageKey;
    const shouldRestore = config.restoreOnEmptyQuery !== false;
    const saveOnSubmit = config.saveOnSubmit !== false;

    function readForm() {
      const values = {};
      fields.forEach((field) => {
        const input = form.querySelector('[name="' + field + '"]');
        values[field] = input && input.value ? input.value : "";
      });
      return values;
    }

    function saveFilters() {
      if (!storageKey) return;
      writeSavedJson(storageKey, readForm());
    }

    if (config.clearButtonSelector && storageKey) {
      const clearButtons = Array.from(document.querySelectorAll(config.clearButtonSelector));
      clearButtons.forEach((button) => {
        button.addEventListener("click", () => window.localStorage.removeItem(storageKey));
      });
    }

    const query = getQueryParams();
    const hasQueryFilters = fields.some((field) => hasValue(query.get(field) || ""));
    const hasManagedFilterParams = fields.some((field) => query.has(field));

    // Only restore when no managed filter params are present at all.
    // If params exist but are blank (e.g., pm=&spc=), treat it as an explicit clear action.
    if (!hasManagedFilterParams && !hasQueryFilters && shouldRestore && storageKey) {
      const saved = readSavedJson(storageKey);
      if (saved && typeof saved === "object") {
        const restored = getQueryParams();
        let changed = false;
        fields.forEach((field) => {
          const value = hasValue(saved[field]) ? saved[field].trim() : "";
          if (value) {
            restored.set(field, value);
            changed = true;
          }
        });
        if (changed) {
          window.location.replace(getPathWithParams(restored));
          return;
        }
      }
    } else if (hasManagedFilterParams || hasQueryFilters) {
      saveFilters();
    }

    fields.forEach((field) => {
      const input = form.querySelector('[name="' + field + '"]');
      if (input) input.addEventListener("change", saveFilters);
    });

    if (saveOnSubmit) {
      form.addEventListener("submit", saveFilters);
    }

    if (config.columns) {
      initColumnState(config.columns);
    }
  }

  window.DotmacUiFilterState = { init: initFilterState };
})();
