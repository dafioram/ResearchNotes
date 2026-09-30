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
