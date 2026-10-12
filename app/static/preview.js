// Hover previews (spec §6.2): resting the pointer on a [[link]] to a
// note shows that note in a small card beside it, so you can see what a
// link is without leaving the page. The card stays while the pointer is
// on the link or the card (to scroll it, or follow a link in it) and goes
// on leaving either, on Escape, or on scrolling the page. Links inside a
// card don't open cards of their own. Each note is fetched once per page.
(function () {
  "use strict";

  var SHOW_DELAY = 350, HIDE_DELAY = 250;
  var cache = {};            // note id -> HTML (or null: no preview)
  var card = null, current = null, showTimer = null, hideTimer = null;

  function noteId(link) {
    var m = /^\/notes\/(\d+)$/.exec(link.getAttribute("href") || "");
    return m ? m[1] : null;
  }

  function fetchPreview(id) {
    if (id in cache) return Promise.resolve(cache[id]);
    return fetch("/notes/" + id + "/preview")
      .then(function (r) { return r.ok ? r.text() : null; })
      .catch(function () { return null; })
      .then(function (html) { cache[id] = html; return html; });
  }

  function place(link) {
    var r = link.getBoundingClientRect();
    var w = card.offsetWidth, h = card.offsetHeight, gap = 6;
    var left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    var below = r.bottom + gap + h <= window.innerHeight - 8;
    var top = below ? r.bottom + gap : Math.max(8, r.top - gap - h);
    card.style.left = left + "px";
    card.style.top = top + "px";
  }

  function hide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    if (card) card.remove();
    card = current = null;
  }

  function show(link) {
    var id = noteId(link);
    if (!id) return;
    fetchPreview(id).then(function (html) {
      if (!html || current !== link || !document.body.contains(link)) return;
      if (card) card.remove();
      card = document.createElement("div");
      card.className = "note-preview";
      card.setAttribute("role", "tooltip");
      card.innerHTML = html;
      card.addEventListener("mouseenter", function () { clearTimeout(hideTimer); });
      card.addEventListener("mouseleave", function () { hideTimer = setTimeout(hide, HIDE_DELAY); });
      document.body.appendChild(card);
      place(link);
    });
  }

  document.addEventListener("mouseover", function (e) {
    var link = e.target.closest && e.target.closest("a.note-ref");
    if (!link || (card && card.contains(link))) return;
    if (link === current) { clearTimeout(hideTimer); return; }
    hide();
    current = link;
    showTimer = setTimeout(function () { show(link); }, SHOW_DELAY);
  });

  document.addEventListener("mouseout", function (e) {
    var link = e.target.closest && e.target.closest("a.note-ref");
    if (!link || link !== current || link.contains(e.relatedTarget)) return;
    clearTimeout(showTimer);
    hideTimer = setTimeout(hide, HIDE_DELAY);
  });

  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
  window.addEventListener("scroll", function () { if (card) hide(); }, { passive: true });
})();
