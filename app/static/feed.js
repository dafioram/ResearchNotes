(function () {
  "use strict";

  function setExpanded(card, on) {
    var btn = card.querySelector(".disclosure-btn");
    var snippet = card.querySelector('[data-role="snippet"]');
    var full = card.querySelector('[data-role="full"]');

    card.classList.toggle("expanded", on);
    if (btn) btn.setAttribute("aria-expanded", on ? "true" : "false");
    if (snippet) snippet.hidden = on;
    if (full) full.hidden = !on;

    if (on && full && full.dataset.loaded !== "true") {
      loadFull(card, full);
    }
  }

  function loadFull(card, full) {
    var url = card.getAttribute("data-fragment-url");
    full.innerHTML = '<p class="loading-hint">Loading&hellip;</p>';
    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("request failed");
        return r.text();
      })
      .then(function (html) {
        full.innerHTML = html;
        full.dataset.loaded = "true";
      })
      .catch(function () {
        var href = "/notes/" + card.getAttribute("data-note-id");
        full.innerHTML =
          '<p class="loading-hint">Couldn\u2019t load this note here. ' +
          '<a href="' + href + '">Open it directly</a>.</p>';
      });
  }

  function toggle(card) {
    setExpanded(card, !card.classList.contains("expanded"));
  }

  document.querySelectorAll(".note-card").forEach(function (card) {
    var btn = card.querySelector(".disclosure-btn");
    if (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        toggle(card);
      });
    }

    var zone = card.querySelector('[data-role="clickzone"]');
    if (zone) {
      zone.addEventListener("click", function (e) {
        if (e.target.closest("a")) return; // let real links navigate normally
        toggle(card);
      });
    }
  });

  // ---- the label chips (spec §6.4) ----
  // A "#prefix-*" chip's caret opens its labels in place; Show all lifts
  // the cap; the filter box narrows every label (groups included) as you
  // type, and clearing it puts things back as they were.
  var chips = document.getElementById("label-chips");
  if (chips) {
    var showAll = document.getElementById("labels-show-all");
    var filterBox = document.getElementById("label-filter");
    var filterCount = document.getElementById("label-filter-count");
    var capped = chips.classList.contains("capped");

    function setOpen(caret, open) {
      var sub = document.getElementById(caret.getAttribute("aria-controls"));
      caret.setAttribute("aria-expanded", open ? "true" : "false");
      caret.textContent = open ? "\u25BE" : "\u25B8";
      if (sub) sub.hidden = !open;
    }

    chips.addEventListener("click", function (e) {
      var caret = e.target.closest(".chip-caret");
      if (!caret) return;
      setOpen(caret, caret.getAttribute("aria-expanded") !== "true");
    });

    if (showAll) {
      showAll.addEventListener("click", function () {
        capped = false;
        chips.classList.remove("capped");
        showAll.hidden = true;
      });
    }

    if (filterBox) {
      // How each group was before filtering, to put back afterwards.
      var carets = Array.prototype.slice.call(chips.querySelectorAll(".chip-caret"));
      var wasOpen = carets.map(function (c) { return c.getAttribute("aria-expanded") === "true"; });

      filterBox.addEventListener("input", function () {
        var typed = filterBox.value.trim().toLowerCase().replace(/^#/, "");
        var shown = 0;
        chips.querySelectorAll(".filtered-out").forEach(function (el) { el.classList.remove("filtered-out"); });
        chips.classList.toggle("capped", capped && !typed);
        if (showAll) showAll.hidden = !capped || !!typed;
        filterCount.hidden = !typed;
        if (!typed) {
          carets.forEach(function (c, i) { setOpen(c, wasOpen[i]); });
          return;
        }
        Array.prototype.forEach.call(chips.children, function (el) {
          if (el.classList.contains("chip-sub")) return;  // handled with its group
          if (el.classList.contains("chip-group")) {
            var sub = document.getElementById("labels-" + el.getAttribute("data-name"));
            var whole = el.getAttribute("data-name").indexOf(typed) !== -1;
            var hits = 0;
            sub.querySelectorAll(".chip").forEach(function (c) {
              var hit = whole || c.getAttribute("data-name").indexOf(typed) !== -1;
              c.classList.toggle("filtered-out", !hit);
              if (hit) hits += 1;
            });
            el.classList.toggle("filtered-out", !hits);
            sub.classList.toggle("filtered-out", !hits);
            setOpen(el.querySelector(".chip-caret"), hits > 0);
            shown += hits;
          } else {
            var match = el.getAttribute("data-name").indexOf(typed) !== -1;
            el.classList.toggle("filtered-out", !match);
            if (match) shown += 1;
          }
        });
        filterCount.textContent = shown + " of " + filterBox.getAttribute("placeholder").match(/\d+/)[0] + " labels";
      });
    }
  }

  // ---- landing on a just-created note ----
  // After Done on a new note, the feed opens on the page containing it with
  // ?focus=<id>: scroll it to the middle and highlight it briefly. The
  // parameter is then dropped from the address so a refresh doesn't repeat it.
  var script = document.currentScript;
  var focusId = script && script.getAttribute("data-focus");
  if (focusId) {
    var target = document.querySelector('.note-card[data-note-id="' + focusId + '"]');
    if (target) {
      target.scrollIntoView({ block: "center" });
      target.classList.add("just-added");
      target.addEventListener("animationend", function () {
        target.classList.remove("just-added");
      }, { once: true });
    }
    var url = new URL(window.location.href);
    url.searchParams.delete("focus");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }

  // ---- never show a stale list after Back ----
  // Pages are sent no-store so Back re-fetches them, but a browser may still
  // restore this page from its back/forward cache; if so, reload. The
  // browser keeps the scroll position across the reload.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) window.location.reload();
  });
})();
