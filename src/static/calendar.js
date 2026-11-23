(function () {
  "use strict";

  const dataScript = document.getElementById("cal-data");
  if (!dataScript) return;

  let cal;
  try {
    cal = JSON.parse(dataScript.textContent);
  } catch (err) {
    return;
  }

  const presetsContainer = document.getElementById("presets");
  const calendarContainer = document.getElementById("calendar");
  const statusContainer = document.getElementById("status");
  const summaryContainer = document.getElementById("summary");
  const editLink = document.getElementById("edit-link");

  const state = {
    entries: new Map(),
    activeTool: null,
  };

  if (Array.isArray(cal.entries)) {
    for (const e of cal.entries) {
      state.entries.set(e.date, {
        minutes: Number(e.minutes),
        holiday: Boolean(e.holiday),
      });
    }
  }

  function clearEl(el) {
    while (el.firstChild) {
      el.removeChild(el.firstChild);
    }
  }

  let statusTimer = null;

  // Shows a small toast. "Saved" fades out by itself; errors and hints stay a little longer.
  function setStatus(text) {
    if (!statusContainer) return;
    if (statusTimer) {
      clearTimeout(statusTimer);
      statusTimer = null;
    }
    statusContainer.textContent = text;
    const isSaving = text === "Saving…";
    const isSaved = text === "Saved";
    statusContainer.classList.toggle("is-error", !isSaving && !isSaved && text !== "");
    statusContainer.classList.toggle("is-visible", text !== "");
    if (isSaved) {
      statusContainer.textContent = "Saved ✓";
      statusTimer = setTimeout(() => statusContainer.classList.remove("is-visible"), 1400);
    } else if (!isSaving && text !== "") {
      statusTimer = setTimeout(() => statusContainer.classList.remove("is-visible"), 4000);
    }
  }

  function formatHours(minutes) {
    const h = minutes / 60;
    return Number(h.toFixed(2)).toString();
  }

  function parseCustomMinutes(val) {
    if (!val || typeof val !== "string") return null;
    const clean = val.trim().replace(",", ".");
    if (!clean) return null;
    const num = Number(clean);
    if (isNaN(num) || num < 0.25 || num > 24) return null;
    return Math.round(num * 60);
  }

  // --- Summary Rendering ---
  function renderSummary(data) {
    if (!summaryContainer) return;
    clearEl(summaryContainer);

    const head = document.createElement("div");
    head.className = "card-head";
    const heading = document.createElement("h2");
    heading.textContent = "Estimate";
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = "paid " + data.pay_month;
    head.appendChild(heading);
    head.appendChild(meta);
    summaryContainer.appendChild(head);

    if (!data.rows || data.rows.length === 0) {
      const emptyNote = document.createElement("p");
      emptyNote.className = "help-text";
      emptyNote.textContent = "Place days on the calendar to see the estimate.";
      summaryContainer.appendChild(emptyNote);
      return;
    }

    let totalMinutes = 0;
    let holidayDays = 0;
    for (const info of state.entries.values()) {
      totalMinutes += info.minutes;
      if (info.holiday) holidayDays += 1;
    }
    const tiles = document.createElement("div");
    tiles.className = "cal-sum-tiles";
    const tileData = [
      [String(data.days), data.days === 1 ? "day" : "days"],
      [formatHours(totalMinutes), "hours"],
      [String(data.sundays), data.sundays === 1 ? "Sunday" : "Sundays"],
      [String(holidayDays), holidayDays === 1 ? "holiday" : "holidays"],
    ];
    for (const [num, word] of tileData) {
      const tile = document.createElement("div");
      tile.className = "cal-sum-tile";
      const b = document.createElement("b");
      b.textContent = num;
      const s = document.createElement("span");
      s.textContent = word;
      tile.appendChild(b);
      tile.appendChild(s);
      tiles.appendChild(tile);
    }
    summaryContainer.appendChild(tiles);

    const rowsWrap = document.createElement("div");
    rowsWrap.className = "cal-sum-rows";
    for (const r of data.rows) {
      if (r.kind === "total") continue; // shown large below
      const row = document.createElement("div");
      row.className = "sum-row sum-" + r.kind;

      const label = document.createElement("span");
      label.textContent = r.label;
      const value = document.createElement("span");
      value.textContent = r.value;

      row.appendChild(label);
      row.appendChild(value);
      rowsWrap.appendChild(row);
    }
    summaryContainer.appendChild(rowsWrap);

    const totalWrap = document.createElement("div");
    totalWrap.className = "cal-sum-total";
    const totalLabel = document.createElement("span");
    totalLabel.className = "label";
    totalLabel.textContent = "Estimated total";
    const total = document.createElement("span");
    total.className = "big-number";
    total.textContent = data.total;
    totalWrap.appendChild(totalLabel);
    totalWrap.appendChild(total);
    summaryContainer.appendChild(totalWrap);
  }

  // --- Save Logic (Debounce & Single In-Flight) ---
  let debounceTimer = null;
  let inFlight = false;
  let pendingSave = false;

  function scheduleSave() {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      triggerSave();
    }, 400);
  }

  async function triggerSave() {
    if (inFlight) {
      pendingSave = true;
      return;
    }
    inFlight = true;
    pendingSave = false;
    setStatus("Saving…");

    try {
      await performSave();
    } catch (err) {
      setStatus("Not saved: " + (err && err.message ? err.message : "network error"));
    } finally {
      inFlight = false;
      if (pendingSave) {
        pendingSave = false;
        scheduleSave();
      }
    }
  }

  async function performSave() {
    const metaCsrf = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = metaCsrf ? metaCsrf.getAttribute("content") : "";

    const daysList = [];
    for (const [date, info] of state.entries.entries()) {
      daysList.push({
        date: date,
        minutes: info.minutes,
        holiday: info.holiday,
      });
    }
    daysList.sort((a, b) => a.date.localeCompare(b.date));

    let resp;
    try {
      resp = await fetch(cal.save_url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ days: daysList }),
      });
    } catch (netErr) {
      setStatus("Not saved: " + (netErr && netErr.message ? netErr.message : "network error"));
      return;
    }

    const contentType = resp.headers.get("content-type") || "";
    if (resp.redirected || !contentType.includes("application/json")) {
      setStatus("Not saved: please log in again");
      return;
    }

    let result;
    try {
      result = await resp.json();
    } catch (parseErr) {
      setStatus("Not saved: please log in again");
      return;
    }

    if (!resp.ok || !result.ok) {
      const errText = result && result.error ? result.error : "HTTP " + resp.status;
      setStatus("Not saved: " + errText);
      return;
    }

    setStatus(result.gcal === "error" ? "Saved, but Google Calendar was not updated (see Settings)" : "Saved");
    renderSummary(result);
    if (editLink && result.rows && result.rows.length > 0) {
      editLink.hidden = false;
    }
  }

  // --- Applying Tools ---
  function applyToolToDate(tool, dateStr) {
    if (!tool) {
      openDayEditor(dateStr);
      return;
    }

    if (tool.type === "erase") {
      state.entries.delete(dateStr);
    } else if (tool.type === "preset") {
      // a tapped preset carries {preset: {...}}; a dropped one carries the fields itself
      const p = tool.preset || tool;
      const minutes = Number(p.minutes);
      if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
        setStatus("That preset has invalid hours.");
        return;
      }
      const isHol = Boolean(p.holiday || (cal.holidays && dateStr in cal.holidays));
      state.entries.set(dateStr, {
        minutes: minutes,
        holiday: isHol,
      });
    } else if (tool.type === "custom") {
      const minutes = Number(tool.minutes);
      if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
        setStatus("Enter hours between 0.25 and 24 first");
        return;
      }
      const isHol = Boolean(cal.holidays && dateStr in cal.holidays);
      state.entries.set(dateStr, {
        minutes: minutes,
        holiday: isHol,
      });
    }

    renderCalendarGrid();
    scheduleSave();
  }

  // --- Presets Bar ---
  let customInput = null;

  // Chip content: colour swatch, label and (optional) hours.
  function chipParts(labelText, metaText) {
    const frag = document.createDocumentFragment();
    const sw = document.createElement("span");
    sw.className = "chip-swatch";
    sw.setAttribute("aria-hidden", "true");
    frag.appendChild(sw);
    const lab = document.createElement("span");
    lab.textContent = labelText;
    frag.appendChild(lab);
    if (metaText) {
      const meta = document.createElement("span");
      meta.className = "chip-meta";
      meta.textContent = metaText;
      frag.appendChild(meta);
    }
    return frag;
  }

  function updatePresetsBarActiveStates() {
    if (!presetsContainer) return;
    const buttons = presetsContainer.querySelectorAll("button");
    for (const btn of buttons) {
      const toolType = btn.getAttribute("data-tool-type");
      let isActive = false;
      if (state.activeTool) {
        if (toolType === "preset" && state.activeTool.type === "preset") {
          isActive = state.activeTool.preset.id === btn.getAttribute("data-preset-id");
        } else if (toolType === "custom" && state.activeTool.type === "custom") {
          isActive = true;
        } else if (toolType === "erase" && state.activeTool.type === "erase") {
          isActive = true;
        }
      }
      btn.classList.toggle("is-active", isActive);
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    }
  }

  function renderPresetsBar() {
    if (!presetsContainer) return;
    clearEl(presetsContainer);

    if (Array.isArray(cal.presets)) {
      for (const p of cal.presets) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "preset-btn";
        btn.setAttribute("data-tool-type", "preset");
        btn.setAttribute("data-preset-id", p.id);
        btn.setAttribute("aria-pressed", "false");
        btn.draggable = true;
        btn.title = p.label + " (" + formatHours(p.minutes) + " h)";
        btn.appendChild(chipParts(p.label, formatHours(p.minutes) + "h"));

        btn.addEventListener("click", () => {
          const same = state.activeTool && state.activeTool.type === "preset" && state.activeTool.preset.id === p.id;
          state.activeTool = same ? null : { type: "preset", preset: p };
          updatePresetsBarActiveStates();
        });

        btn.addEventListener("dragstart", (e) => {
          e.dataTransfer.setData(
            "text/plain",
            JSON.stringify({
              tool: {
                type: "preset",
                id: p.id,
                minutes: p.minutes,
                holiday: p.holiday,
              },
            })
          );
        });

        presetsContainer.appendChild(btn);
      }
    }

    // Custom Tool Control
    const customWrapper = document.createElement("div");
    customWrapper.className = "preset-custom";

    customInput = document.createElement("input");
    customInput.type = "text";
    customInput.className = "preset-custom-input";
    customInput.placeholder = "Custom h";
    customInput.inputMode = "decimal";
    customInput.setAttribute("aria-label", "Custom hours, e.g. 6.5");

    const customBtn = document.createElement("button");
    customBtn.type = "button";
    customBtn.className = "preset-btn";
    customBtn.setAttribute("data-tool-type", "custom");
    customBtn.setAttribute("aria-pressed", "false");
    customBtn.draggable = true;
    customBtn.textContent = "Use";

    function getCustomMinutesOrAlert() {
      const mins = parseCustomMinutes(customInput.value);
      if (mins === null) {
        setStatus("Enter hours between 0.25 and 24 first");
        return null;
      }
      return mins;
    }

    customBtn.addEventListener("click", () => {
      const mins = getCustomMinutesOrAlert();
      if (mins !== null) {
        state.activeTool = { type: "custom", minutes: mins };
        updatePresetsBarActiveStates();
      }
    });

    customInput.addEventListener("input", () => {
      if (state.activeTool && state.activeTool.type === "custom") {
        const mins = parseCustomMinutes(customInput.value);
        if (mins !== null) {
          state.activeTool.minutes = mins;
        }
      }
    });

    customBtn.addEventListener("dragstart", (e) => {
      const mins = getCustomMinutesOrAlert();
      if (mins === null) {
        e.preventDefault();
        return;
      }
      e.dataTransfer.setData(
        "text/plain",
        JSON.stringify({
          tool: {
            type: "custom",
            minutes: mins,
          },
        })
      );
    });

    customWrapper.appendChild(customInput);
    customWrapper.appendChild(customBtn);
    presetsContainer.appendChild(customWrapper);

    // Erase Tool Button
    const eraseBtn = document.createElement("button");
    eraseBtn.type = "button";
    eraseBtn.className = "preset-btn preset-erase-btn";
    eraseBtn.setAttribute("data-tool-type", "erase");
    eraseBtn.setAttribute("aria-pressed", "false");
    eraseBtn.draggable = true;
    eraseBtn.appendChild(chipParts("Erase", ""));

    eraseBtn.addEventListener("click", () => {
      const same = state.activeTool && state.activeTool.type === "erase";
      state.activeTool = same ? null : { type: "erase" };
      updatePresetsBarActiveStates();
    });

    eraseBtn.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData(
        "text/plain",
        JSON.stringify({
          tool: {
            type: "erase",
          },
        })
      );
    });

    presetsContainer.appendChild(eraseBtn);
    updatePresetsBarActiveStates();
  }

  // --- Calendar Grid (Google Calendar style month view) ---
  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  // Greyed-out day from the previous or next month: shown for context, not clickable.
  function outsideCell(dayNum, weekdayIdx) {
    const cell = document.createElement("div");
    cell.className = "cal-day is-outside" + (weekdayIdx >= 5 ? " is-weekend" : "");
    cell.setAttribute("aria-hidden", "true");
    const top = document.createElement("div");
    top.className = "cal-day-top";
    const num = document.createElement("span");
    num.className = "cal-day-num";
    num.textContent = String(dayNum);
    top.appendChild(num);
    cell.appendChild(top);
    return cell;
  }

  function makeChip(className, mainText, subText, title) {
    const chip = document.createElement("div");
    chip.className = "cal-chip " + className;
    if (title) chip.title = title;
    const main = document.createElement("span");
    main.className = "cal-chip-main";
    main.textContent = mainText;
    chip.appendChild(main);
    if (subText) {
      const sub = document.createElement("span");
      sub.className = "cal-chip-sub";
      sub.textContent = subText;
      chip.appendChild(sub);
    }
    return chip;
  }

  function renderCalendarGrid() {
    if (!calendarContainer) return;
    clearEl(calendarContainer);

    const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    for (const name of weekdays) {
      const th = document.createElement("div");
      th.className = "cal-header-cell" + (name === "Sat" || name === "Sun" ? " is-weekend" : "");
      th.textContent = name;
      calendarContainer.appendChild(th);
    }

    const year = Number(cal.work_month.slice(0, 4));
    const month = Number(cal.work_month.slice(5, 7));
    const offset = Number(cal.weekday_offset) || 0;
    const daysInMonth = Number(cal.days_in_month) || 0;
    const prevMonthDays = new Date(year, month - 1, 0).getDate();

    for (let i = 0; i < offset; i++) {
      calendarContainer.appendChild(outsideCell(prevMonthDays - offset + 1 + i, i));
    }

    const now = new Date();
    const todayStr = now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());

    for (let day = 1; day <= daysInMonth; day++) {
      const dateStr = cal.work_month + "-" + pad2(day);
      const weekdayIdx = (offset + (day - 1)) % 7;
      const isSunday = weekdayIdx === 6;
      const holidayName =
        cal.holidays && Object.prototype.hasOwnProperty.call(cal.holidays, dateStr)
          ? cal.holidays[dateStr]
          : null;
      const isHoliday = holidayName !== null;
      const entry = state.entries.get(dateStr);
      const isWorked = Boolean(entry);

      const cell = document.createElement("div");
      cell.className = "cal-day";
      cell.setAttribute("data-date", dateStr);
      cell.setAttribute("role", "gridcell");
      cell.setAttribute("tabindex", "0");

      if (weekdayIdx >= 5) cell.classList.add("is-weekend");
      if (isSunday) cell.classList.add("is-sunday");
      if (isHoliday) {
        cell.classList.add("is-holiday");
        cell.title = holidayName;
      }
      if (dateStr === todayStr) cell.classList.add("is-today");
      if (isWorked) {
        cell.classList.add("is-worked");
        if (entry.holiday) cell.classList.add("is-holiday-entry");
        cell.setAttribute("aria-label", dateStr + ": " + formatHours(entry.minutes) + " hours" + (entry.holiday ? ", holiday" : ""));
      } else {
        cell.setAttribute("aria-label", dateStr + (holidayName ? ": " + holidayName : ""));
      }

      const topRow = document.createElement("div");
      topRow.className = "cal-day-top";
      const numSpan = document.createElement("span");
      numSpan.className = "cal-day-num";
      numSpan.textContent = String(day);
      topRow.appendChild(numSpan);
      cell.appendChild(topRow);

      const chips = document.createElement("div");
      chips.className = "cal-chips";

      // Public holiday: a light "all-day event" chip, like a holiday calendar.
      if (isHoliday) {
        chips.appendChild(makeChip("cal-chip-public", holidayName, "", holidayName));
      }

      // Worked day: a solid event chip with the hours, plus a clear button.
      if (isWorked) {
        const chip = makeChip(
          entry.holiday ? "cal-chip-holiday" : "cal-chip-work",
          formatHours(entry.minutes) + "h",
          entry.holiday ? "Holiday" : "Work",
          formatHours(entry.minutes) + " h" + (entry.holiday ? " (holiday)" : "")
        );
        const clearBtn = document.createElement("button");
        clearBtn.type = "button";
        clearBtn.className = "cal-day-clear";
        clearBtn.setAttribute("aria-label", "Clear " + dateStr);
        clearBtn.textContent = "×";
        clearBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          state.entries.delete(dateStr);
          renderCalendarGrid();
          scheduleSave();
        });
        chip.appendChild(clearBtn);
        chip.classList.add("is-editable");
        chip.addEventListener("click", (e) => {
          e.stopPropagation();
          openDayEditor(dateStr);
        });
        chips.appendChild(chip);
      }
      cell.appendChild(chips);

      // Tap-to-paint events
      cell.addEventListener("click", () => {
        applyToolToDate(state.activeTool, dateStr);
      });
      cell.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          applyToolToDate(state.activeTool, dateStr);
        }
      });

      // Drag-and-drop events
      cell.addEventListener("dragover", (e) => {
        e.preventDefault();
        cell.classList.add("is-drop-target");
      });
      cell.addEventListener("dragleave", () => {
        cell.classList.remove("is-drop-target");
      });
      cell.addEventListener("drop", (e) => {
        e.preventDefault();
        cell.classList.remove("is-drop-target");
        const raw = e.dataTransfer.getData("text/plain");
        if (!raw) return;
        let parsed;
        try {
          parsed = JSON.parse(raw);
        } catch (err) {
          return;
        }
        if (parsed && parsed.tool) {
          applyToolToDate(parsed.tool, dateStr);
        }
      });

      calendarContainer.appendChild(cell);
    }

    // Fill the last week with days of the next month.
    const used = offset + daysInMonth;
    const trailing = (7 - (used % 7)) % 7;
    for (let i = 1; i <= trailing; i++) {
      calendarContainer.appendChild(outsideCell(i, (used + i - 1) % 7));
    }
  }

  // --- Day editor (Google Calendar style popover) ---
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  let editorDialog = null;

  function normalMinutes() {
    if (Array.isArray(cal.presets)) {
      for (const p of cal.presets) {
        if (p.id === "normal") return Number(p.minutes);
      }
    }
    return 450;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function openDayEditor(dateStr) {
    if (editorDialog) {
      editorDialog.remove();
      editorDialog = null;
    }
    const entry = state.entries.get(dateStr);
    const holidayName =
      cal.holidays && Object.prototype.hasOwnProperty.call(cal.holidays, dateStr) ? cal.holidays[dateStr] : null;
    const y = Number(dateStr.slice(0, 4));
    const m = Number(dateStr.slice(5, 7));
    const d = Number(dateStr.slice(8, 10));
    const weekday = WEEKDAYS[new Date(y, m - 1, d).getDay()];

    const dlg = el("dialog", "day-editor");
    dlg.setAttribute("aria-label", "Edit " + dateStr);
    const form = el("form", "day-editor-form");
    form.method = "dialog";

    const head = el("div", "day-editor-head");
    const title = el("h2", "", weekday + " " + d + " " + MONTHS[m - 1]);
    head.appendChild(title);
    const closeBtn = el("button", "day-editor-close", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.addEventListener("click", () => dlg.close());
    head.appendChild(closeBtn);
    form.appendChild(head);

    if (holidayName) {
      form.appendChild(el("p", "day-editor-public", holidayName));
    }

    const hoursLabel = el("label", "day-editor-field", "Hours worked");
    const hoursInput = el("input");
    hoursInput.type = "text";
    hoursInput.inputMode = "decimal";
    hoursInput.placeholder = "e.g. 6.5";
    hoursInput.value = formatHours(entry ? entry.minutes : normalMinutes());
    hoursLabel.appendChild(hoursInput);
    form.appendChild(hoursLabel);

    const holLabel = el("label", "day-editor-check");
    const holInput = el("input");
    holInput.type = "checkbox";
    holInput.checked = entry ? entry.holiday : Boolean(holidayName);
    holLabel.appendChild(holInput);
    holLabel.appendChild(document.createTextNode(" Holiday (paid double)"));
    form.appendChild(holLabel);

    const error = el("p", "day-editor-error");
    error.hidden = true;
    form.appendChild(error);

    const actions = el("div", "day-editor-actions");
    if (entry) {
      const removeBtn = el("button", "btn-danger-link", "Remove day");
      removeBtn.type = "button";
      removeBtn.addEventListener("click", () => {
        state.entries.delete(dateStr);
        dlg.close();
        renderCalendarGrid();
        scheduleSave();
      });
      actions.appendChild(removeBtn);
    }
    const saveBtn = el("button", "day-editor-save", "Save");
    saveBtn.type = "submit";
    actions.appendChild(saveBtn);
    form.appendChild(actions);

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const mins = parseCustomMinutes(hoursInput.value);
      if (mins === null) {
        error.textContent = "Enter hours between 0.25 and 24.";
        error.hidden = false;
        hoursInput.focus();
        return;
      }
      state.entries.set(dateStr, { minutes: mins, holiday: holInput.checked });
      dlg.close();
      renderCalendarGrid();
      scheduleSave();
    });

    // Click on the dark backdrop closes it.
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg) dlg.close();
    });
    dlg.addEventListener("close", () => {
      dlg.remove();
      if (editorDialog === dlg) editorDialog = null;
    });

    dlg.appendChild(form);
    document.body.appendChild(dlg);
    editorDialog = dlg;
    if (typeof dlg.showModal === "function") {
      dlg.showModal();
    } else {
      dlg.setAttribute("open", "");
    }
    hoursInput.focus();
    hoursInput.select();
  }

  // --- Initial Render ---
  renderPresetsBar();
  renderCalendarGrid();
  renderSummary(cal);
})();
