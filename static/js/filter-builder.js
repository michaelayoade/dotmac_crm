(function () {
  const OPERATORS = [
    { value: "=", label: "Equals" },
    { value: "!=", label: "Not Equals" },
    { value: "like", label: "Like" },
    { value: "not like", label: "Not Like" },
    { value: "in", label: "In" },
    { value: "not in", label: "Not In" },
    { value: ">", label: "Greater Than" },
    { value: "<", label: "Less Than" },
    { value: ">=", label: "Greater Than or Equal" },
    { value: "<=", label: "Less Than or Equal" },
    { value: "is", label: "Is" },
    { value: "is not", label: "Is Not" },
  ];

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function fieldByName(fields, name) {
    return fields.find((item) => item.name === name) || fields[0];
  }

  function operatorsForField(field) {
    if (!field) return OPERATORS;
    if (Array.isArray(field.operators) && field.operators.length) {
      return OPERATORS.filter((operator) => field.operators.includes(operator.value));
    }
    return OPERATORS;
  }

  function normalizeScalar(raw, field, operator) {
    const text = String(raw == null ? "" : raw).trim();
    if ((operator === "is" || operator === "is not") && !text) {
      return null;
    }
    if (field.type === "number") {
      const parsed = Number(text);
      return Number.isNaN(parsed) ? text : parsed;
    }
    if (field.type === "boolean") {
      return text === "true";
    }
    return text;
  }

  function normalizeValue(raw, field, operator) {
    if (operator === "in" || operator === "not in") {
      return String(raw || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
        .map((item) => normalizeScalar(item, field, "="));
    }
    return normalizeScalar(raw, field, operator);
  }

  function rawValueForRowValue(value, operator) {
    if (operator === "in" || operator === "not in") {
      if (Array.isArray(value)) return value.join(", ");
      return "";
    }
    if (value == null) return "";
    return String(value);
  }

  function parsePayload(rawFilters, fields) {
    if (!rawFilters) return [];
    let parsed;
    try {
      parsed = JSON.parse(rawFilters);
    } catch (_error) {
      return [];
    }
    if (!Array.isArray(parsed)) return [];

    const rows = [];
    parsed.forEach((entry) => {
      if (Array.isArray(entry) && entry.length === 4) {
        rows.push({
          logic: "and",
          field: String(entry[1]),
          operator: String(entry[2]),
          value: rawValueForRowValue(entry[3], String(entry[2])),
        });
        return;
      }
      if (entry && typeof entry === "object" && Array.isArray(entry.or)) {
        entry.or.forEach((term) => {
          if (!Array.isArray(term) || term.length !== 4) return;
          rows.push({
            logic: "or",
            field: String(term[1]),
            operator: String(term[2]),
            value: rawValueForRowValue(term[3], String(term[2])),
          });
        });
      }
    });

    return rows
      .filter((row) => fields.some((field) => field.name === row.field))
      .map((row) => {
        const field = fieldByName(fields, row.field);
        const supportedOps = operatorsForField(field).map((item) => item.value);
        const operator = supportedOps.includes(row.operator) ? row.operator : supportedOps[0];
        return { ...row, operator };
      });
  }

  function ensureHiddenInput(form, filtersFieldName, initialValue) {
    let input = form.querySelector('input[name="' + filtersFieldName + '"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = filtersFieldName;
      form.appendChild(input);
    }
    if (!input.value && initialValue) {
      input.value = initialValue;
    }
    return input;
  }

  function buildPayload(rows, fields, doctype) {
    const andRows = [];
    const orRows = [];

    rows.forEach((row) => {
      const field = fieldByName(fields, row.field);
      if (!field || !row.operator) return;
      if ((row.operator === "is" || row.operator === "is not") && String(row.value || "").trim() === "") {
        const term = [doctype, field.name, row.operator, null];
        if (row.logic === "or") orRows.push(term);
        else andRows.push(term);
        return;
      }

      const normalized = normalizeValue(row.value, field, row.operator);
      if (row.operator !== "is" && row.operator !== "is not") {
        if (Array.isArray(normalized) && normalized.length === 0) return;
        if (!Array.isArray(normalized) && String(normalized == null ? "" : normalized).trim() === "") return;
      }
      const term = [doctype, field.name, row.operator, normalized];
      if (row.logic === "or") orRows.push(term);
      else andRows.push(term);
    });

    const payload = andRows.slice();
    if (orRows.length) payload.push({ or: orRows });
    return payload;
  }

  function createDefaultRow(fields) {
    const firstField = fields[0];
    return {
      logic: "and",
      field: firstField ? firstField.name : "",
      operator: firstField ? operatorsForField(firstField)[0].value : "=",
      value: "",
    };
  }

  function valueControl(field, value, operator) {
    const baseClass =
      "w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white";
    if (field.type === "select" && Array.isArray(field.options)) {
      const optionsHtml = field.options
        .map((option) => {
          const selected = String(option.value) === String(value) ? ' selected="selected"' : "";
          return '<option value="' + escapeHtml(option.value) + '"' + selected + ">" + escapeHtml(option.label) + "</option>";
        })
        .join("");
      return '<select data-filter-builder-control data-role="value" class="' + baseClass + '">' + optionsHtml + "</select>";
    }
    if (field.type === "boolean") {
      const trueSelected = String(value) === "true" ? ' selected="selected"' : "";
      const falseSelected = String(value) === "false" ? ' selected="selected"' : "";
      return (
        '<select data-filter-builder-control data-role="value" class="' +
        baseClass +
        '">' +
        '<option value="">Select…</option>' +
        '<option value="true"' +
        trueSelected +
        ">True</option>" +
        '<option value="false"' +
        falseSelected +
        ">False</option>" +
        "</select>"
      );
    }

    const type = field.type === "number" ? "number" : field.type === "date" ? "date" : "text";
    const placeholder = operator === "in" || operator === "not in" ? "Comma-separated values" : "Value";
    return (
      '<input data-filter-builder-control data-role="value" type="' +
      type +
      '" class="' +
      baseClass +
      '" value="' +
      escapeHtml(value || "") +
      '" placeholder="' +
      escapeHtml(placeholder) +
      '">'
    );
  }

  function renderRows(state, config) {
    const { mount, fields } = state;
    if (!mount) return;

    const rowsHtml = state.rows
      .map((row, index) => {
        const field = fieldByName(fields, row.field);
        const availableOperators = operatorsForField(field);
        const fieldOptions = fields
          .map((item) => {
            const selected = item.name === row.field ? ' selected="selected"' : "";
            return '<option value="' + escapeHtml(item.name) + '"' + selected + ">" + escapeHtml(item.label) + "</option>";
          })
          .join("");
        const operatorOptions = availableOperators
          .map((item) => {
            const selected = item.value === row.operator ? ' selected="selected"' : "";
            return '<option value="' + escapeHtml(item.value) + '"' + selected + ">" + escapeHtml(item.label) + "</option>";
          })
          .join("");

        const logicCell =
          index === 0
            ? '<div class="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Where</div>'
            : '<select data-filter-builder-control data-role="logic" class="w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white"><option value="and"' +
              (row.logic !== "or" ? ' selected="selected"' : "") +
              '>AND</option><option value="or"' +
              (row.logic === "or" ? ' selected="selected"' : "") +
              '>OR</option></select>';

        return (
          '<div class="grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2 md:grid-cols-12 dark:border-slate-700 dark:bg-slate-900/30" data-row-index="' +
          index +
          '">' +
          '<div class="md:col-span-2">' +
          logicCell +
          "</div>" +
          '<div class="md:col-span-3"><select data-filter-builder-control data-role="field" class="w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white">' +
          fieldOptions +
          "</select></div>" +
          '<div class="md:col-span-3"><select data-filter-builder-control data-role="operator" class="w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-600 dark:bg-slate-700 dark:text-white">' +
          operatorOptions +
          "</select></div>" +
          '<div class="md:col-span-3">' +
          valueControl(field, row.value, row.operator) +
          "</div>" +
          '<div class="md:col-span-1"><button type="button" data-action="remove-row" class="w-full rounded-lg border border-rose-200 px-2 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-50 dark:border-rose-900/40 dark:text-rose-300">Remove</button></div>' +
          "</div>"
        );
      })
      .join("");

    mount.innerHTML =
      '<div class="space-y-2">' +
      (rowsHtml || '<p class="text-xs text-slate-500 dark:text-slate-400">No advanced filters. Click Add Row.</p>') +
      "</div>" +
      '<div class="mt-2 flex flex-wrap gap-2">' +
      '<button type="button" data-action="add-row" class="inline-flex items-center rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200">Add Row</button>' +
      '<button type="button" data-action="clear-rows" class="inline-flex items-center rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200">Clear Advanced</button>' +
      '<span class="text-xs text-slate-500 dark:text-slate-400">Use OR to group rows together. Rows without OR use AND.</span>' +
      "</div>";

    const containerRows = Array.from(mount.querySelectorAll("[data-row-index]"));
    containerRows.forEach((container) => {
      const index = Number(container.getAttribute("data-row-index") || "0");
      const row = state.rows[index];
      if (!row) return;

      const logicInput = container.querySelector('[data-role="logic"]');
      if (logicInput) {
        logicInput.addEventListener("change", (event) => {
          row.logic = event.target.value === "or" ? "or" : "and";
        });
      }

      const fieldInput = container.querySelector('[data-role="field"]');
      if (fieldInput) {
        fieldInput.addEventListener("change", (event) => {
          const newField = fieldByName(fields, event.target.value);
          row.field = newField.name;
          const availableOperators = operatorsForField(newField);
          row.operator = availableOperators[0].value;
          row.value = "";
          renderRows(state, config);
        });
      }

      const operatorInput = container.querySelector('[data-role="operator"]');
      if (operatorInput) {
        operatorInput.addEventListener("change", (event) => {
          row.operator = event.target.value;
          row.value = "";
          renderRows(state, config);
        });
      }

      const valueInput = container.querySelector('[data-role="value"]');
      if (valueInput) {
        valueInput.addEventListener("input", (event) => {
          row.value = event.target.value;
        });
        valueInput.addEventListener("change", (event) => {
          row.value = event.target.value;
        });
      }

      const removeButton = container.querySelector('[data-action="remove-row"]');
      if (removeButton) {
        removeButton.addEventListener("click", () => {
          state.rows.splice(index, 1);
          renderRows(state, config);
        });
      }
    });

    const addButton = mount.querySelector('[data-action="add-row"]');
    if (addButton) {
      addButton.addEventListener("click", () => {
        state.rows.push(createDefaultRow(fields));
        renderRows(state, config);
      });
    }

    const clearButton = mount.querySelector('[data-action="clear-rows"]');
    if (clearButton) {
      clearButton.addEventListener("click", () => {
        state.rows = [];
        if (state.hiddenInput) state.hiddenInput.value = "";
        renderRows(state, config);
      });
    }
  }

  function init(config) {
    const form = document.getElementById(config.formId);
    const mount = document.getElementById(config.mountId);
    const fields = Array.isArray(config.fields) ? config.fields : [];
    if (!form || !mount || !fields.length || !config.doctype) return;

    const initialFilters = config.initialFilters || "";
    const hiddenInput = ensureHiddenInput(form, config.filtersFieldName || "filters", initialFilters);
    const state = {
      form,
      mount,
      fields,
      hiddenInput,
      rows: parsePayload(hiddenInput.value || initialFilters, fields),
    };

    renderRows(state, config);

    form.addEventListener("submit", () => {
      const payload = buildPayload(state.rows, fields, config.doctype);
      hiddenInput.value = payload.length ? JSON.stringify(payload) : "";
    });
  }

  window.DotmacFilterBuilder = { init };
})();
