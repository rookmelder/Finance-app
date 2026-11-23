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

    setStatus("Saved");
    renderSummary(result);
    if (editLink && result.rows && result.rows.length > 0) {
      editLink.hidden = false;
    }
  }

  // --- Applying Tools ---
  function applyToolToDate(tool, dateStr) {
    if (!tool) {
      setStatus("Pick a preset first, then tap the days.");
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
          state.activeTool = { type: "preset", preset: p };
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
      state.activeTool = { type: "erase" };
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

  // --- Calendar Grid ---
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

    const offset = Number(cal.weekday_offset) || 0;
    for (let i = 0; i < offset; i++) {
      const empty = document.createElement("div");
      empty.className = "cal-empty";
      calendarContainer.appendChild(empty);
    }

    const now = new Date();
    const todayStr =
      now.getFullYear() +
      "-" +
      String(now.getMonth() + 1).padStart(2, "0") +
      "-" +
      String(now.getDate()).padStart(2, "0");

    const daysInMonth = Number(cal.days_in_month) || 0;
    for (let day = 1; day <= daysInMonth; day++) {
      const dateStr =
        cal.work_month + "-" + String(day).padStart(2, "0");
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

      if (weekdayIdx >= 5) {
        cell.classList.add("is-weekend");
      }
      if (isSunday) {
        cell.classList.add("is-sunday");
      }
      if (isHoliday) {
        cell.classList.add("is-holiday");
        cell.title = holidayName;
      }
      if (dateStr === todayStr) {
        cell.classList.add("is-today");
      }
      if (isWorked) {
        cell.classList.add("is-worked");
        if (entry.holiday) cell.classList.add("is-holiday-entry");
        cell.setAttribute("aria-label", dateStr + ": " + formatHours(entry.minutes) + " hours" + (entry.holiday ? ", holiday" : ""));
      } else {
        cell.setAttribute("aria-label", dateStr + (holidayName ? ": " + holidayName : ""));
      }

      // Day Top Row (Number + Clear button)
      const topRow = document.createElement("div");
      topRow.className = "cal-day-top";

      const numSpan = document.createElement("span");
      numSpan.className = "cal-day-num";
      numSpan.textContent = String(day);
      topRow.appendChild(numSpan);

      if (isHoliday) {
        const dot = document.createElement("span");
        dot.className = "cal-holiday-dot";
        dot.setAttribute("aria-hidden", "true");
        topRow.appendChild(dot);
      }

      if (isWorked) {
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
        topRow.appendChild(clearBtn);
      }
      cell.appendChild(topRow);

      // Holiday Label if holiday
      if (isHoliday) {
        const holLabel = document.createElement("span");
        holLabel.className = "cal-holiday-label";
        holLabel.textContent = holidayName;
        holLabel.title = holidayName;
        cell.appendChild(holLabel);
      }

      // Placed Day Info (Hours + H badge)
      if (isWorked) {
        const infoRow = document.createElement("div");
        infoRow.className = "cal-day-info";

        const hoursSpan = document.createElement("span");
        hoursSpan.className = "cal-day-hours";
        hoursSpan.textContent = formatHours(entry.minutes);
        const unit = document.createElement("small");
        unit.textContent = "h";
        hoursSpan.appendChild(unit);
        infoRow.appendChild(hoursSpan);

        if (entry.holiday) {
          const hBadge = document.createElement("span");
          hBadge.className = "cal-badge-holiday";
          hBadge.textContent = "H";
          hBadge.title = "Holiday";
          infoRow.appendChild(hBadge);
        }
        cell.appendChild(infoRow);
      }

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
      });

      cell.addEventListener("drop", (e) => {
        e.preventDefault();
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
  }

  // --- Initial Render ---
  renderPresetsBar();
  renderCalendarGrid();
  renderSummary(cal);
})();
