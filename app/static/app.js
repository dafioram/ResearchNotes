/*
 * The note page (note.html): View / Edit switch, saving in place, Done and
 * Cancel, the unsaved-changes guard, the label picker and attachments.
 *
 * Saving never leaves the page. Save and Ctrl+S save and keep editing;
 * Done, and flipping the switch to View, save and then show View. On a
 * page that started as a new note, the first save creates the note and
 * the page carries on as that note; Done then returns to the feed, landing
 * on the new note.
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

  textarea.addEventListener("input", refreshStatus);
  dateInput.addEventListener("input", refreshStatus);
  dateInput.addEventListener("change", refreshStatus);

  window.addEventListener("beforeunload", function (e) {
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
        if (!state.noteId) becomeSavedNote(data);
        applySaved(data);
        lastSavedMessage = "Saved " + data.saved_at;
        return data;
      })
      .then(
        function (data) { state.saving = null; refreshStatus(); return data; },
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

  var cancelBtn = document.getElementById("cancel-btn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () {
      if (isDirty() && !window.confirm("Discard unsaved changes?")) return;
      textarea.value = state.savedBody;
      dateInput.value = state.savedDate;
      refreshStatus();
      if (!state.noteId) leaveTo("/");                  // never saved: nothing to show
      else if (state.startedNew) leaveTo(state.feedUrl);
      else setMode("view");
    });
  }

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
  // Label picker: insert #label at the cursor
  // ------------------------------------------------------------------

  document.querySelectorAll(".label-picker button[data-label]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var insertion = "#" + btn.getAttribute("data-label") + " ";
      var start = textarea.selectionStart;
      var end = textarea.selectionEnd;
      var before = textarea.value.slice(0, start);
      var after = textarea.value.slice(end);
      var text = (before.length > 0 && !/\s$/.test(before) ? " " : "") + insertion;
      textarea.value = before + text + after;
      var cursor = start + text.length;
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
      refreshStatus();
    });
  });

  if (isMac) {
    document.querySelectorAll("[data-mod-key]").forEach(function (el) {
      el.textContent = "⌘";
    });
  }

  // Opening a page in Edit mode puts the cursor in the editor.
  if (mode() === "edit") textarea.focus({ preventScroll: true });

  // ------------------------------------------------------------------
  // Attachments: upload and remove in place, never touching the editor.
  // The whole section is swapped in after a new note's first save, so
  // everything here is looked up at event time, not bound up front.
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

  function sendAttachmentForm(formEl) {
    var uploadForm = document.getElementById("attachment-upload");
    return fetch(formEl.action, {
      method: "POST",
      body: new FormData(formEl),
      headers: { "X-Requested-With": "fetch" },
    })
      .catch(function () {
        throw new Error("Couldn’t reach the app. Is it still running?");
      })
      .then(function (r) {
        if (r.status === 413) {
          var max = uploadForm && uploadForm.getAttribute("data-max-bytes");
          throw new Error("That file is over the " + (max ? formatMB(+max) : "upload") + " limit.");
        }
        return r.json().then(
          function (data) {
            var panel = document.getElementById("attachments-panel");
            if (data.html !== undefined && panel) panel.innerHTML = data.html;
            var viewPane = document.getElementById("view-pane");
            if (data.view_html && viewPane) viewPane.innerHTML = data.view_html;
            if (!data.ok) throw new Error(data.message);
            return data;
          },
          function () {
            throw new Error("Something went wrong; attachments weren’t changed.");
          }
        );
      });
  }

  document.addEventListener("submit", function (e) {
    var uploadForm = e.target.closest("#attachment-upload");
    if (uploadForm) {
      e.preventDefault();
      var fileInput = uploadForm.querySelector('input[type="file"]');
      var button = uploadForm.querySelector('button[type="submit"]');
      var maxBytes = +uploadForm.getAttribute("data-max-bytes") || 0;
      var file = fileInput.files[0];
      if (!file) { attachmentStatus("Choose a file to upload.", true); return; }
      if (maxBytes && file.size > maxBytes) {
        attachmentStatus(file.name + " is over the " + formatMB(maxBytes) + " limit.", true);
        return;
      }
      button.disabled = true;
      attachmentStatus("Uploading " + file.name + "…");
      sendAttachmentForm(uploadForm)
        .then(function (data) { uploadForm.reset(); attachmentStatus(data.message); })
        .catch(function (err) { attachmentStatus(err.message, true); })
        .then(function () { button.disabled = false; });
      return;
    }

    var removeForm = e.target.closest("form[data-attachment-remove]");
    if (removeForm) {
      e.preventDefault();
      var removeButton = removeForm.querySelector("button");
      if (removeButton) removeButton.disabled = true;
      sendAttachmentForm(removeForm)
        .then(function (data) { attachmentStatus(data.message); })
        .catch(function (err) {
          attachmentStatus(err.message, true);
          if (removeButton) removeButton.disabled = false;
        });
    }
  });
})();
