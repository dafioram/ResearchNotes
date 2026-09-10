(function () {
  "use strict";

  // ---- label picker: insert #label at the cursor in the body textarea ----
  var textarea = document.getElementById("body");
  document.querySelectorAll(".label-picker button[data-label]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!textarea) return;
      var name = btn.getAttribute("data-label");
      var insertion = "#" + name + " ";
      var start = textarea.selectionStart;
      var end = textarea.selectionEnd;
      var before = textarea.value.slice(0, start);
      var after = textarea.value.slice(end);
      var needsLeadingSpace = before.length > 0 && !/\s$/.test(before);
      var text = (needsLeadingSpace ? " " : "") + insertion;
      textarea.value = before + text + after;
      var cursor = start + text.length;
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
    });
  });

  // ---- attachment search-as-you-type ----
  var searchBox = document.getElementById("attach-search");
  var resultsBox = document.getElementById("attach-results");
  if (searchBox && resultsBox) {
    var scriptTag = document.currentScript;
    var attachUrl = scriptTag.getAttribute("data-attach-url");
    var searchUrl = scriptTag.getAttribute("data-search-url");
    var timer = null;

    searchBox.addEventListener("input", function () {
      clearTimeout(timer);
      var q = searchBox.value.trim();
      if (!q) {
        resultsBox.innerHTML = "";
        return;
      }
      timer = setTimeout(function () {
        fetch(searchUrl + "?q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (items) {
            resultsBox.innerHTML = "";
            items.forEach(function (item) {
              var b = document.createElement("button");
              b.type = "button";
              b.textContent = item.filename + "  (" + Math.round(item.size / 1024) + " KB)";
              b.addEventListener("click", function () {
                var form = document.createElement("form");
                form.method = "post";
                form.action = attachUrl;
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "attachment_id";
                input.value = item.id;
                form.appendChild(input);
                document.body.appendChild(form);
                form.submit();
              });
              resultsBox.appendChild(b);
            });
          });
      }, 250);
    });
  }
})();
