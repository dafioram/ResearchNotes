/*
 * The note page (note.html): View / Edit switch, saving in place, Done and
 * Cancel, the unsaved-changes guard, suggestions for [[links and
 * #labels while typing, pasted cells made into tables, and attachments.
 *
 * Saving never leaves the page. Save and Ctrl+S save and keep editing;
 * Done, and flipping the switch to View, save and then show View. On a
 * page that started as a new note, the first save creates the note and
 * the page carries on as that note; Done then returns to the feed, landing
 * on the new note. Once a note exists it also saves itself a few seconds
 * after typing stops, and until text is saved it's kept in this browser
 * as a draft, offered back if the page is opened again (spec §4.3).
 */
(function () {
  "use strict";

  var pageEl = document.getElementById("note-page");
  var form = document.getElementById("note-form");
  var textarea = document.getElementById("body");
  var dateInput = document.getElementById("sort_date");
  var statusEl = document.getElementById("save-status");
  if (!pageEl || !form || !textarea || !dateInput) return;

  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || "");
  var state = {
    noteId: pageEl.getAttribute("data-note-id") || null,
    startedNew: pageEl.getAttribute("data-started-new") === "true",
    feedUrl: pageEl.getAttribute("data-feed-url") || "/",
    savedBody: textarea.value,
    savedDate: dateInput.value,
    saving: null,       // the in-flight save, so repeat presses don't double-save
    leaving: false,     // set when we navigate away on purpose
    // The text when this stretch of editing began, for Cancel to go back to.
    openedBody: textarea.value,
    openedDate: dateInput.value,
  };

  // ------------------------------------------------------------------
  // Mode
  // ------------------------------------------------------------------

  function mode() {
    return document.body.classList.contains("mode-edit") ? "edit" : "view";
  }

  function modeUrl(m) {
    return m === "edit" ? "/notes/" + state.noteId + "/edit" : "/notes/" + state.noteId;
  }

  function setMode(m) {
    if (m === "edit" && mode() !== "edit") {
      state.openedBody = textarea.value;
      state.openedDate = dateInput.value;
    }
    document.body.classList.toggle("mode-edit", m === "edit");
    document.body.classList.toggle("mode-view", m === "view");
    var radio = document.querySelector('.mode-switch input[value="' + m + '"]');
    if (radio) radio.checked = true;
    if (state.noteId) {
      // Replace, don't push: refresh keeps the mode, Back doesn't step
      // through every flip.
      history.replaceState(null, "", modeUrl(m));
    }
    if (m === "edit") textarea.focus({ preventScroll: true });
  }

  document.querySelectorAll('.mode-switch input[name="mode"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      if (!radio.checked) return;
      if (radio.value === "edit") { setMode("edit"); return; }
      // Flipping to View saves first (same as Done); stay in Edit if it fails.
      save().then(
        function () { setMode("view"); },
        function () { setMode("edit"); }
      );
    });
  });

  // ------------------------------------------------------------------
  // Unsaved-changes tracking
  // ------------------------------------------------------------------

  function isDirty() {
    return textarea.value !== state.savedBody || dateInput.value !== state.savedDate;
  }

  function showStatus(message, kind) {
    if (!statusEl) return;
    statusEl.textContent = message;
    statusEl.classList.toggle("error", kind === "error");
    statusEl.classList.toggle("dirty", kind === "dirty");
  }

  var lastSavedMessage = "";
  function refreshStatus() {
    if (state.saving) return;
    if (isDirty()) showStatus("Unsaved changes", "dirty");
    else showStatus(lastSavedMessage, null);
  }

  function edited() {
    refreshStatus();
    scheduleDraft();
    scheduleAutosave();
  }

  textarea.addEventListener("input", edited);
  dateInput.addEventListener("input", edited);
  dateInput.addEventListener("change", edited);

  window.addEventListener("beforeunload", function (e) {
    writeDraft();
    if (state.leaving || !isDirty()) return;
    e.preventDefault();
    e.returnValue = ""; // older browsers need this to show the prompt
  });

  // ------------------------------------------------------------------
  // Saving
  // ------------------------------------------------------------------

  function save() {
    if (state.saving) return state.saving;
    if (!form.reportValidity()) return Promise.reject(new Error("invalid"));
    if (state.noteId && !isDirty()) return Promise.resolve(null);

    var sentBody = textarea.value;
    var sentDate = dateInput.value;
    showStatus("Saving…", null);

    state.saving = fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "fetch" },
    })
      .catch(function () {
        throw new Error("Couldn’t reach the app. Is it still running? Nothing was saved.");
      })
      .then(function (r) {
        return r.json().catch(function () {
          throw new Error("Something went wrong; the note wasn’t saved.");
        });
      })
      .then(function (data) {
        if (!data.ok) throw new Error(data.message || "The note wasn’t saved.");
        state.savedBody = sentBody;
        state.savedDate = sentDate;
        if (!state.noteId) {
          becomeSavedNote(data);
          dropDraft(null);
          state.openedBody = sentBody;   // Cancel goes back to the first save
          state.openedDate = sentDate;
        }
        applySaved(data);
        writeDraft();  // gone if everything's saved; the newer text if typing went on
        lastSavedMessage = "Saved " + data.saved_at;
        return data;
      })
      .then(
        function (data) {
          state.saving = null;
          refreshStatus();
          if (isDirty()) scheduleAutosave();  // typed on while it was saving
          return data;
        },
        function (err) { state.saving = null; showStatus(err.message, "error"); throw err; }
      );
    return state.saving;
  }

  // Update the page from a successful save.
  function applySaved(data) {
    var viewPane = document.getElementById("view-pane");
    if (viewPane && data.view_html !== undefined) viewPane.innerHTML = data.view_html;
    var metaDate = document.getElementById("meta-date");
    var metaLines = document.getElementById("meta-lines");
    if (metaDate) metaDate.textContent = data.sort_date;
    if (metaLines) metaLines.textContent = data.line_count + " line" + (data.line_count === 1 ? "" : "s");
    state.feedUrl = data.feed_url;
    setGraphEnabled(data.has_connections);
    labelsPromise = null;  // the save may have added labels
  }

  // The first save of a new note: it now has an id, so the page turns into
  // that note's page -- title, address, switch, attachments, graph/delete.
  function becomeSavedNote(data) {
    state.noteId = String(data.note_id);
    pageEl.setAttribute("data-note-id", state.noteId);
    form.action = data.update_url;
    history.replaceState(null, "", data.edit_url);
    document.title = "No. " + state.noteId + " · Research Notes";
    var title = document.getElementById("note-title");
    if (title) title.textContent = "No. " + state.noteId;
    var slot = document.getElementById("attachments-slot");
    if (slot) slot.innerHTML = data.attachments_html;
    var common = document.getElementById("common-actions");
    if (common) common.innerHTML = data.common_html;
    document.body.classList.remove("is-new");
  }

  function setGraphEnabled(enabled) {
    var btn = document.getElementById("graph-btn");
    if (!btn) return;
    btn.classList.toggle("disabled", !enabled);
    if (enabled) {
      btn.setAttribute("href", btn.getAttribute("data-href"));
      btn.removeAttribute("aria-disabled");
      btn.removeAttribute("title");
    } else {
      btn.removeAttribute("href");
      btn.setAttribute("aria-disabled", "true");
      btn.setAttribute("title", btn.getAttribute("data-no-connections-title"));
    }
  }

  function leaveTo(url) {
    state.leaving = true;
    window.location.href = url;
  }

  // Save button (a submit button for #note-form) and Ctrl+S both come here.
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    save().catch(function () {});
  });

  document.addEventListener("keydown", function (e) {
    var mod = isMac ? e.metaKey : e.ctrlKey;
    if (!mod || e.altKey || e.shiftKey || e.key.toLowerCase() !== "s") return;
    e.preventDefault(); // never the browser's "Save page as"
    if (mode() === "edit") save().catch(function () {});
  });

  var doneBtn = document.getElementById("done-btn");
  if (doneBtn) {
    doneBtn.addEventListener("click", function () {
      save().then(function () {
        if (state.startedNew) leaveTo(state.feedUrl);  // lands on the new note
        else setMode("view");
      }, function () {});
    });
  }

  // Cancel undoes this stretch of editing: back to the text as it was when
  // Edit was opened (for a new note, its first save) -- saving that back,
  // since autosave may have saved some of the changes already.
  var cancelBtn = document.getElementById("cancel-btn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () {
      var changed = textarea.value !== state.openedBody || dateInput.value !== state.openedDate;
      if (!state.noteId) {                               // never saved: nothing to keep
        if (changed && !window.confirm("Discard this note?")) return;
        dropDraft(null);
        leaveTo("/");
        return;
      }
      if (changed && !window.confirm("Undo your changes since you started editing?")) return;
      clearTimeout(autosaveTimer);
      textarea.value = state.openedBody;
      dateInput.value = state.openedDate;
      refreshStatus();
      (isDirty() ? save() : Promise.resolve()).then(function () {
        dropDraft(state.noteId);
        if (state.startedNew) leaveTo(state.feedUrl);
        else setMode("view");
      }, function () {});
    });
  }

  // ------------------------------------------------------------------
  // Autosave: once a note exists, it saves itself a few seconds after
  // typing stops. (A new note is created by its first Save, by hand, so
  // stray keystrokes never create notes.) Saves that close together merge
  // into one History entry and one version (§11.2).
  // ------------------------------------------------------------------

  var AUTOSAVE_DELAY = 3000;
  var autosaveTimer = null;

  function scheduleAutosave() {
    clearTimeout(autosaveTimer);
    if (!state.noteId) return;
    autosaveTimer = setTimeout(function () {
      // checkValidity, not reportValidity: no "please fill in" bubble
      // popping up by itself; a cleared date waits for a manual save.
      if (mode() !== "edit" || !isDirty() || !form.checkValidity()) return;
      save().catch(function () {});
    }, AUTOSAVE_DELAY);
  }

  // ------------------------------------------------------------------
  // Drafts: until it's saved, what's typed is kept in this browser's
  // storage, so a crash, a killed tab or a dead battery can't lose it.
  // Opening the note (or New note) again offers it back. Drafts older
  // than 30 days are dropped.
  // ------------------------------------------------------------------

  var DRAFT_PREFIX = "rn-draft:";
  var DRAFT_MAX_AGE = 30 * 24 * 3600 * 1000;
  var draftTimer = null;
  var draftOffered = false;   // while one is offered, don't overwrite it

  function draftKey(id) { return DRAFT_PREFIX + (id || "new"); }

  function readDraft(id) {
    try { return JSON.parse(window.localStorage.getItem(draftKey(id)) || "null"); }
    catch (e) { return null; }
  }

  function dropDraft(id) {
    try { window.localStorage.removeItem(draftKey(id)); } catch (e) { /* storage off */ }
  }

  function writeDraft() {
    clearTimeout(draftTimer);
    if (draftOffered) return;
    if (!isDirty()) { dropDraft(state.noteId); return; }
    try {
      window.localStorage.setItem(draftKey(state.noteId), JSON.stringify({
        body: textarea.value,
        sort_date: dateInput.value,
        base: state.savedBody,     // what was saved when it was written
        at: Date.now(),
      }));
    } catch (e) { /* storage off or full: nothing more to do */ }
  }

  function scheduleDraft() {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(writeDraft, 300);
  }

  function pruneDrafts() {
    try {
      var storage = window.localStorage, old = [];
      for (var i = 0; i < storage.length; i++) {
        var key = storage.key(i);
        if (key.indexOf(DRAFT_PREFIX) !== 0) continue;
        var d = JSON.parse(storage.getItem(key) || "null");
        if (!d || Date.now() - d.at > DRAFT_MAX_AGE) old.push(key);
      }
      old.forEach(function (key) { storage.removeItem(key); });
    } catch (e) { /* storage off */ }
  }

  function offerDraft() {
    var d = readDraft(state.noteId);
    if (!d || typeof d.body !== "string") return;
    if (d.body === textarea.value && d.sort_date === dateInput.value) {
      dropDraft(state.noteId);   // it was saved after all
      return;
    }
    draftOffered = true;
    var when = new Date(d.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    var banner = document.createElement("div");
    banner.className = "draft-banner";
    banner.setAttribute("role", "status");
    var text = document.createElement("p");
    text.textContent = (state.noteId
        ? "Unsaved changes to this note from " + when + " were kept in this browser."
        : "A new note you hadn’t saved, from " + when + ", was kept in this browser.") +
      (state.noteId && d.base !== state.savedBody
        ? " The note has been saved since; restoring replaces that text." : "");
    var restore = document.createElement("button");
    restore.type = "button";
    restore.className = "btn";
    restore.textContent = "Restore";
    var discard = document.createElement("button");
    discard.type = "button";
    discard.className = "btn-quiet";
    discard.textContent = "Discard";
    banner.appendChild(text);
    banner.appendChild(restore);
    banner.appendChild(discard);
    pageEl.parentNode.insertBefore(banner, pageEl);

    restore.addEventListener("click", function () {
      draftOffered = false;
      banner.remove();
      if (mode() !== "edit") setMode("edit");
      textarea.value = d.body;
      dateInput.value = d.sort_date || dateInput.value;
      edited();
    });
    discard.addEventListener("click", function () {
      draftOffered = false;
      banner.remove();
      writeDraft();   // keeps anything typed meanwhile, else drops the old draft
    });
  }

  // A phone or tablet may never fire beforeunload; this comes first.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") writeDraft();
  });

  pruneDrafts();
  offerDraft();

  // Delete (form is swapped in after a new note's first save, so delegate).
  document.addEventListener("submit", function (e) {
    var del = e.target.closest("#delete-form");
    if (!del) return;
    if (!window.confirm(del.getAttribute("data-confirm"))) { e.preventDefault(); return; }
    state.leaving = true;
  });

  // A disabled "View graph" is an <a> without href, but guard anyway.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("#graph-btn.disabled");
    if (btn) e.preventDefault();
  });

  // ------------------------------------------------------------------
  // Suggestions while typing, in one pop-up under the cursor:
  //
  // [[ links (spec §6.2): typing [[ lists notes to link to. Words search
  // titles and text, a number matches note numbers, nothing typed shows
  // the latest notes. Picking one inserts [[id]]; the last choice is
  // always "link later", which inserts [[later]] (with what was typed as
  // its hint).
  //
  // #labels (spec §6.1): typing # and a letter where a label can start
  // lists the labels in use that match -- same name first, then names
  // starting with it, then names containing it, most-used first within
  // each. Picking one completes it. Enter on a label that's already
  // typed in full just starts a new line.
  //
  // Up/Down choose, Enter or Tab pick, Escape closes until the next one.
  // ------------------------------------------------------------------

  var REF_TRIGGER = /\[\[([^\[\]\n]{0,80})$/;
  // The label rule's start (§6.1): line start or whitespace, '#', a letter.
  var LABEL_TRIGGER = /(?:^|\s)#(\p{L}[\p{L}\p{N}_.\-]{0,80})$/u;
  var LABEL_CHARS = /^[\p{L}\p{N}_.\-]*/u;
  var LABEL_LIMIT = 8;

  var popup = document.createElement("div");
  popup.className = "ref-popup";
  popup.id = "ref-popup";
  popup.setAttribute("role", "listbox");
  popup.hidden = true;
  document.body.appendChild(popup);
  textarea.setAttribute("aria-autocomplete", "list");
  textarea.setAttribute("aria-controls", "ref-popup");

  var lookup = {
    kind: null,       // "ref" or "label"
    start: -1,        // where the [[ or # being completed starts
    query: null,      // what's typed after it
    items: [],        // {insert, el, exact}
    active: 0,
    dismissed: -1,    // Escape closes the list for this [[ or # only
    seq: 0,           // drops replies to older keystrokes
    timer: null,
  };

  function completionContext() {
    if (textarea.selectionStart !== textarea.selectionEnd) return null;
    var caret = textarea.selectionStart;
    var before = textarea.value.slice(0, caret);
    var m = REF_TRIGGER.exec(before);
    if (m) return { kind: "ref", start: caret - m[0].length, end: caret, query: m[1] };
    m = LABEL_TRIGGER.exec(before);
    if (m) return { kind: "label", start: caret - m[1].length - 1, end: caret, query: m[1] };
    return null;
  }

  function closeLookup() {
    popup.hidden = true;
    lookup.kind = null;
    lookup.start = -1;
    lookup.query = null;
    lookup.items = [];
    textarea.removeAttribute("aria-activedescendant");
    clearTimeout(lookup.timer);
  }

  function updateLookup() {
    var ctx = mode() === "edit" ? completionContext() : null;
    if (!ctx) lookup.dismissed = -1;
    if (!ctx || ctx.start === lookup.dismissed) { closeLookup(); return; }
    if (ctx.kind === lookup.kind && ctx.start === lookup.start && ctx.query === lookup.query) return;
    lookup.kind = ctx.kind;
    lookup.start = ctx.start;
    lookup.query = ctx.query;
    clearTimeout(lookup.timer);
    if (ctx.kind === "label") {
      var seq = ++lookup.seq;
      loadLabels().then(function (labels) {
        if (seq === lookup.seq) showLabels(ctx.query, labels);
      });
    } else {
      lookup.timer = setTimeout(function () { fetchNotes(ctx.query); }, 120);
    }
  }

  // ---- #labels ----

  // Fetched once, when first needed; a save may add labels, so it's
  // fetched again after one (applySaved).
  var labelsPromise = null;
  function loadLabels() {
    if (!labelsPromise) {
      labelsPromise = fetch("/api/labels", { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (data) { return data.labels || []; })
        .catch(function () { labelsPromise = null; return []; });
    }
    return labelsPromise;
  }

  function matchLabels(query, labels) {
    var q = query.toLowerCase();
    var exact = [], starts = [], contains = [];
    labels.forEach(function (l) {   // already most-used first
      if (l.name === q) exact.push(l);
      else if (l.name.indexOf(q) === 0) starts.push(l);
      else if (l.name.indexOf(q) > 0) contains.push(l);
    });
    return exact.concat(starts, contains).slice(0, LABEL_LIMIT);
  }

  function showLabels(query, labels) {
    var found = matchLabels(query, labels);
    if (!found.length) { popup.hidden = true; lookup.items = []; return; }  // a new label
    popup.textContent = "";
    popup.setAttribute("aria-label", "Labels");
    lookup.items = [];
    found.forEach(function (l) {
      var el = option(lookup.items.length, [
        ["ref-option-title ref-option-label", "#" + l.name],
        ["ref-option-date", l.count + " note" + (l.count === 1 ? "" : "s")],
      ]);
      popup.appendChild(el);
      lookup.items.push({ insert: "#" + l.name, el: el, exact: l.name === query.toLowerCase() });
    });
    openPopup();
  }

  // ---- [[ links ----

  function fetchNotes(query) {
    var seq = ++lookup.seq;
    var url = "/api/notes/lookup?q=" + encodeURIComponent(query) +
      (state.noteId ? "&exclude=" + state.noteId : "");
    fetch(url, { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (seq !== lookup.seq || lookup.query !== query) return;
        showNotes(query, data.notes || []);
      })
      .catch(function () { if (seq === lookup.seq) showNotes(query, []); });
  }

  function laterInsert(query) {
    var hint = query.trim();
    if (/^later\b/i.test(hint)) hint = hint.replace(/^later\s*:?\s*/i, "");
    if (/^\d+$/.test(hint)) hint = "";
    return hint ? "[[later: " + hint + "]]" : "[[later]]";
  }

  function showNotes(query, notes) {
    popup.textContent = "";
    popup.setAttribute("aria-label", "Notes to link");
    lookup.items = [];
    notes.forEach(function (n) {
      var el = option(lookup.items.length, [
        ["ref-option-id", "No. " + n.id],
        ["ref-option-title", n.title],
        ["ref-option-date", n.sort_date],
      ]);
      popup.appendChild(el);
      lookup.items.push({ insert: "[[" + n.id + "]]", el: el });
    });
    if (!notes.length && query.trim() && !/^later\b/i.test(query.trim())) {
      var none = document.createElement("div");
      none.className = "ref-empty";
      none.textContent = "No notes match “" + query.trim() + "”.";
      popup.appendChild(none);
    }
    var later = laterInsert(query);
    var laterEl = option(lookup.items.length, [
      ["ref-option-id", "later"],
      ["ref-option-title", "Link later: " + later],
    ]);
    laterEl.classList.add("ref-option-later");
    popup.appendChild(laterEl);
    lookup.items.push({ insert: later, el: laterEl });
    openPopup();
  }

  // ---- the pop-up ----

  function option(index, parts) {
    var el = document.createElement("div");
    el.className = "ref-option";
    el.id = "ref-option-" + index;
    el.setAttribute("role", "option");
    el.setAttribute("data-index", index);
    parts.forEach(function (p) {
      var span = document.createElement("span");
      span.className = p[0];
      span.textContent = p[1];
      el.appendChild(span);
    });
    return el;
  }

  function openPopup() {
    setActive(0);
    popup.hidden = false;
    placePopup();
  }

  function setActive(i) {
    if (!lookup.items.length) return;
    lookup.active = (i + lookup.items.length) % lookup.items.length;
    lookup.items.forEach(function (item, j) {
      item.el.classList.toggle("active", j === lookup.active);
      item.el.setAttribute("aria-selected", j === lookup.active ? "true" : "false");
    });
    var el = lookup.items[lookup.active].el;
    textarea.setAttribute("aria-activedescendant", el.id);
    el.scrollIntoView({ block: "nearest" });
  }

  function choose(i) {
    var item = lookup.items[i];
    var ctx = completionContext();
    if (!item || !ctx || ctx.kind !== lookup.kind) { closeLookup(); return; }
    var text = textarea.value, end = ctx.end, insert = item.insert;
    if (ctx.kind === "ref") {
      if (text.slice(end, end + 2) === "]]") end += 2;  // already closed
    } else {
      end += LABEL_CHARS.exec(text.slice(end))[0].length;  // the rest of the word
      if (!/^\s/.test(text.slice(end))) insert += " ";
    }
    textarea.focus();
    textarea.setSelectionRange(ctx.start, end);
    // insertText keeps the editor's undo history; setRangeText is the fallback.
    if (!document.execCommand("insertText", false, insert)) {
      textarea.setRangeText(insert, ctx.start, end, "end");
      refreshStatus();
    }
    closeLookup();
  }

  // Where the caret is on screen: a hidden copy of the editor holding the
  // text up to the caret, styled the same, with a marker at the end.
  var MIRRORED = ["boxSizing", "width", "borderTopWidth", "borderRightWidth", "borderBottomWidth",
    "borderLeftWidth", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "fontFamily",
    "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing", "wordSpacing", "tabSize",
    "textIndent", "textTransform"];

  function caretRect(pos) {
    var style = window.getComputedStyle(textarea);
    var mirror = document.createElement("div");
    MIRRORED.forEach(function (p) { mirror.style[p] = style[p]; });
    mirror.style.position = "absolute";
    mirror.style.visibility = "hidden";
    mirror.style.whiteSpace = "pre-wrap";
    mirror.style.overflowWrap = "break-word";
    mirror.style.top = "0";
    mirror.style.left = "-9999px";
    mirror.textContent = textarea.value.slice(0, pos);
    var marker = document.createElement("span");
    marker.textContent = "​";
    mirror.appendChild(marker);
    document.body.appendChild(mirror);
    var box = textarea.getBoundingClientRect();
    var rect = {
      left: box.left + marker.offsetLeft - textarea.scrollLeft,
      top: box.top + marker.offsetTop - textarea.scrollTop,
      height: marker.offsetHeight,
    };
    document.body.removeChild(mirror);
    return rect;
  }

  function placePopup() {
    if (popup.hidden || lookup.start < 0) return;
    var at = caretRect(lookup.start);
    var width = popup.offsetWidth, height = popup.offsetHeight;
    var left = Math.max(8, Math.min(at.left, window.innerWidth - width - 8));
    var top = at.top + at.height + 4;
    if (top + height > window.innerHeight - 8) top = Math.max(8, at.top - height - 4);
    popup.style.left = left + "px";
    popup.style.top = top + "px";
  }

  textarea.addEventListener("input", updateLookup);
  textarea.addEventListener("click", updateLookup);
  textarea.addEventListener("keyup", function (e) {
    if (/^(ArrowLeft|ArrowRight|Home|End)$/.test(e.key)) updateLookup();
  });
  textarea.addEventListener("blur", closeLookup);
  textarea.addEventListener("scroll", placePopup);
  window.addEventListener("scroll", placePopup);
  window.addEventListener("resize", placePopup);

  textarea.addEventListener("keydown", function (e) {
    if (popup.hidden || !lookup.items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive(lookup.active + (e.key === "ArrowDown" ? 1 : -1));
    } else if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (e.key === "Enter" && lookup.items[lookup.active].exact) {
        closeLookup();  // the label is typed in full: Enter is a new line
        return;
      }
      e.preventDefault();
      choose(lookup.active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      lookup.dismissed = lookup.start;
      closeLookup();
    }
  });

  popup.addEventListener("mousedown", function (e) {
    e.preventDefault();  // keep the cursor in the editor
    var el = e.target.closest("[data-index]");
    if (el) choose(+el.getAttribute("data-index"));
  });

  if (isMac) {
    document.querySelectorAll("[data-mod-key]").forEach(function (el) {
      el.textContent = "⌘";
    });
  }

  // Opening a page in Edit mode puts the cursor in the editor.
  if (mode() === "edit") textarea.focus({ preventScroll: true });

  // ------------------------------------------------------------------
  // Pasting cells makes a table (spec §5.2). Spreadsheets copy cells as
  // tab-separated text; exported data is often comma-separated. Either
  // becomes a pipe table when it has two or more rows of the same number
  // of cells (two or more), none empty in the first row -- and, with
  // commas, no cell starting with a space, which is how prose ("Hello,
  // world") differs from data. Not inside a ``` code block. The paste
  // goes in as it was first and the table then replaces it, so Ctrl+Z
  // gives back exactly what was pasted.
  // ------------------------------------------------------------------

  // Rows of cells; quoted cells may hold the separator, "" or a newline.
  function parseDelimited(text, sep) {
    var rows = [], row = [], cell = "", quoted = false, inQuotes = false, spaced = false;
    function endCell() {
      if (!quoted && cell.charAt(0) === " ") spaced = true;
      row.push(cell);
      cell = "";
      quoted = false;
    }
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      if (inQuotes) {
        if (ch !== '"') cell += ch;
        else if (text.charAt(i + 1) === '"') { cell += '"'; i++; }
        else inQuotes = false;
      } else if (ch === '"' && cell === "") {
        inQuotes = quoted = true;
      } else if (ch === sep) {
        endCell();
      } else if (ch === "\n") {
        endCell();
        rows.push(row);
        row = [];
      } else {
        cell += ch;
      }
    }
    if (inQuotes) return null;  // an unclosed quote: not data
    endCell();
    rows.push(row);
    return { rows: rows, spaced: spaced };
  }

  var NUMBER = /^[-+−]?[\d.,]+%?$/;

  function pipeTable(rows) {
    var cells = rows.map(function (row) {
      return row.map(function (c) { return c.replace(/\s*\n\s*/g, " ").trim().replace(/\|/g, "\\|"); });
    });
    var width = cells[0].length;
    var widths = [], numeric = [];
    for (var col = 0; col < width; col++) {
      var w = 3, isNumber = false, allNumbers = true;
      cells.forEach(function (row, r) {
        w = Math.max(w, row[col].length);
        if (r === 0 || row[col] === "") return;
        if (NUMBER.test(row[col])) isNumber = true; else allNumbers = false;
      });
      widths.push(w);
      numeric.push(isNumber && allNumbers);  // numbers line up on the right
    }
    function line(row) {
      return "| " + row.map(function (c, col) {
        return numeric[col] ? " ".repeat(widths[col] - c.length) + c : c + " ".repeat(widths[col] - c.length);
      }).join(" | ") + " |";
    }
    var rule = "|" + widths.map(function (w, col) {
      return numeric[col] ? "-".repeat(w + 1) + ":" : "-".repeat(w + 2);
    }).join("|") + "|";
    return [line(cells[0]), rule].concat(cells.slice(1).map(line)).join("\n");
  }

  function tableFromPaste(text) {
    if (text.indexOf("\n") < 0) return null;
    var sep = text.indexOf("\t") >= 0 ? "\t" : ",";
    var parsed = parseDelimited(text, sep);
    if (!parsed || (sep === "," && parsed.spaced)) return null;
    var rows = parsed.rows, width = rows[0].length;
    if (rows.length < 2 || width < 2) return null;
    if (rows[0].some(function (c) { return c.trim() === ""; })) return null;
    if (rows.some(function (row) { return row.length !== width; })) return null;
    return pipeTable(rows);
  }

  function insertText(text) {
    // insertText keeps the editor's undo history; setRangeText is the fallback.
    if (!document.execCommand("insertText", false, text)) {
      textarea.setRangeText(text, textarea.selectionStart, textarea.selectionEnd, "end");
      refreshStatus();
    }
  }

  textarea.addEventListener("paste", function (e) {
    if (!e.clipboardData) return;
    var text = e.clipboardData.getData("text/plain").replace(/\r\n?/g, "\n").replace(/\n+$/, "");
    var start = textarea.selectionStart, before = textarea.value.slice(0, start);
    if ((before.match(/```/g) || []).length % 2) return;   // inside a code block
    var table = tableFromPaste(text);
    if (!table) return;
    e.preventDefault();
    var after = textarea.value.slice(textarea.selectionEnd);
    insertText(text);                                       // as pasted: the undo step back
    textarea.setSelectionRange(start, start + text.length);
    // The table on lines of its own, ended by a blank line.
    var lead = before === "" || /\n$/.test(before) ? "" : "\n";
    var trail = after === "" || /^\n\n/.test(after) ? "" : /^\n/.test(after) ? "\n" : "\n\n";
    insertText(lead + table + trail);
  });

  // ------------------------------------------------------------------
  // Attachments: drop files anywhere on the page while editing.
  // Uploading and removing happen in place, never touching the editor.
  // The attachment section is swapped in after a new note's first save,
  // so everything here is looked up at event time, not bound up front.
  // ------------------------------------------------------------------

  function formatMB(bytes) {
    return Math.round(bytes / (1024 * 1024)) + " MB";
  }

  function attachmentStatus(message, isError) {
    var el = document.getElementById("attachment-status");
    if (!el) return;
    el.textContent = message;
    el.classList.toggle("error", !!isError);
  }

  function afterAttachmentChange(data) {
    var panel = document.getElementById("attachments-panel");
    if (data.html !== undefined && panel) panel.innerHTML = data.html;
    var viewPane = document.getElementById("view-pane");
    if (data.view_html && viewPane) viewPane.innerHTML = data.view_html;
  }

  // POST to an attachment endpoint; resolves with the JSON reply or
  // rejects with an Error whose message is fit to show.
  function postAttachment(url, body, maxBytes) {
    return fetch(url, { method: "POST", body: body, headers: { "X-Requested-With": "fetch" } })
      .catch(function () {
        throw new Error("Couldn’t reach the app. Is it still running?");
      })
      .then(function (r) {
        if (r.status === 413) {
          throw new Error("over the " + (maxBytes ? formatMB(maxBytes) : "upload") + " limit");
        }
        return r.json().then(
          function (data) {
            afterAttachmentChange(data);
            if (!data.ok) throw new Error(data.message);
            return data;
          },
          function () { throw new Error("Something went wrong; attachments weren’t changed."); }
        );
      });
  }

  // Upload dropped files one after another, then say how it went.
  function uploadFiles(files) {
    var section = document.getElementById("attachments-section");
    if (!section || !files.length) return;
    var url = section.getAttribute("data-upload-url");
    var maxBytes = +section.getAttribute("data-max-bytes") || 0;
    var attached = [], problems = [];

    var chain = Promise.resolve();
    files.forEach(function (file, i) {
      chain = chain.then(function () {
        if (maxBytes && file.size > maxBytes) {
          problems.push(file.name + " is over the " + formatMB(maxBytes) + " limit");
          return;
        }
        attachmentStatus("Uploading " + file.name +
          (files.length > 1 ? " (" + (i + 1) + " of " + files.length + ")" : "") + "…");
        var body = new FormData();
        body.append("file", file, file.name);
        return postAttachment(url, body, maxBytes).then(
          function (data) { attached.push(data.message); },
          function (err) {
            var msg = err.message;
            problems.push(/^over the/.test(msg) ? file.name + " is " + msg : file.name + ": " + msg);
          }
        );
      });
    });

    chain.then(function () {
      var parts = [];
      if (attached.length === 1) parts.push(attached[0]);
      else if (attached.length > 1) parts.push("Attached " + attached.length + " files.");
      if (problems.length) parts.push(problems.join("; ") + ".");
      attachmentStatus(parts.join(" "), problems.length > 0);
    });
  }

  // Remove buttons live inside the list, which is replaced after every change.
  document.addEventListener("submit", function (e) {
    var removeForm = e.target.closest("form[data-attachment-remove]");
    if (!removeForm) return;
    e.preventDefault();
    var button = removeForm.querySelector("button");
    if (button) button.disabled = true;
    postAttachment(removeForm.action, new FormData(removeForm), 0)
      .then(function (data) { attachmentStatus(data.message); })
      .catch(function (err) {
        attachmentStatus(err.message, true);
        if (button) button.disabled = false;
      });
  });

  // ---- the drop target: the whole page ----
  // While a file is dragged over the window, an overlay says what dropping
  // will do. Only drags carrying files count (dragging selected text inside
  // the editor is left alone). The browser's default for a dropped file is
  // to open it in place of the page -- which would throw away unsaved text
  // -- so that default is always cancelled here.

  var overlay = document.createElement("div");
  overlay.className = "drop-overlay";
  overlay.hidden = true;
  overlay.innerHTML = '<div class="drop-overlay-box"><p class="drop-overlay-title"></p><p class="drop-overlay-text"></p></div>';
  document.body.appendChild(overlay);

  function carriesFiles(e) {
    var types = e.dataTransfer && e.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, "Files") !== -1;
  }

  // What a drop would do right now: attach, or why it can't.
  function dropState() {
    if (mode() !== "edit") {
      return { ok: false, title: "Switch to Edit to attach files", text: "Files are attached while editing a note." };
    }
    if (!document.getElementById("attachments-section")) {
      return { ok: false, title: "Save the note first", text: "Files can be attached once the note has been saved." };
    }
    return { ok: true, title: "Drop to attach to No. " + state.noteId, text: "Several files at once is fine." };
  }

  var dragDepth = 0;
  function showOverlay() {
    var s = dropState();
    overlay.classList.toggle("refuse", !s.ok);
    overlay.querySelector(".drop-overlay-title").textContent = s.title;
    overlay.querySelector(".drop-overlay-text").textContent = s.text;
    overlay.hidden = false;
  }
  function hideOverlay() {
    dragDepth = 0;
    overlay.hidden = true;
  }

  window.addEventListener("dragenter", function (e) {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    dragDepth += 1;
    if (dragDepth === 1) showOverlay();
  });
  window.addEventListener("dragover", function (e) {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = dropState().ok ? "copy" : "none";
  });
  window.addEventListener("dragleave", function (e) {
    if (!carriesFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) overlay.hidden = true;
  });
  window.addEventListener("drop", function (e) {
    if (!carriesFiles(e)) return;
    e.preventDefault();   // never let the browser open the file
    var s = dropState();
    if (!s.ok) {
      // Leave the explanation up briefly: in View there's no status line.
      dragDepth = 0;
      showOverlay();
      setTimeout(function () { if (dragDepth === 0) overlay.hidden = true; }, 1800);
      return;
    }
    hideOverlay();
    uploadFiles(Array.prototype.slice.call(e.dataTransfer.files));
  });
})();
