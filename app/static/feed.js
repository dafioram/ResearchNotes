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
        var link = card.querySelector(".meta-link");
        var href = link ? link.getAttribute("href") : "#";
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
})();
