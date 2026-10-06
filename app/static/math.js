// Math (spec §5). The renderer leaves TeX as typed, dollars and all, in
// <span class="math math-inline"> / <span class="math math-display">;
// KaTeX draws it here -- on load, and wherever rendered text is added
// later (a feed card opened in place, the view after a save). Without
// KaTeX, or on an error, the TeX simply stays as typed.
(function () {
  "use strict";

  var OPTIONS = { throwOnError: false, trust: false, maxSize: 20, maxExpand: 500 };

  function draw(el) {
    if (el.hasAttribute("data-tex")) return;            // drawn already
    var source = el.textContent;
    var display = el.classList.contains("math-display");
    var fence = display ? 2 : 1;
    el.setAttribute("data-tex", source);
    el.title = source;                                    // the TeX, on hover
    try {
      window.katex.render(source.slice(fence, -fence), el,
                          Object.assign({ displayMode: display }, OPTIONS));
    } catch (e) {
      el.textContent = source;
    }
  }

  function drawIn(root) {
    if (!window.katex || root.nodeType !== 1 && root !== document) return;
    if (root.matches && root.matches(".math")) draw(root);
    root.querySelectorAll(".math").forEach(draw);
  }

  drawIn(document);
  new MutationObserver(function (changes) {
    changes.forEach(function (change) { change.addedNodes.forEach(drawIn); });
  }).observe(document.body, { childList: true, subtree: true });
})();
