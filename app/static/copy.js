// Copy buttons (spec §9.5): an attachment's Copy puts the text that shows
// it in a note -- ![caption](/files/<hash>) -- on the clipboard. On every
// page, since attachments are listed wherever a note is shown. The
// Clipboard API needs https or localhost; over plain http on the network
// the older copy command does the job.
(function () {
  "use strict";

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    var scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    scratch.remove();
    return ok ? Promise.resolve() : Promise.reject(new Error("copy refused"));
  }

  document.addEventListener("click", function (e) {
    var button = e.target.closest("button[data-copy]");
    if (!button) return;
    e.preventDefault();
    e.stopPropagation();   // inside a feed card: copy, don't expand
    var label = button.textContent;
    copyText(button.getAttribute("data-copy")).then(
      function () { button.textContent = "Copied"; },
      function () { button.textContent = "Couldn’t copy"; }
    ).then(function () {
      setTimeout(function () { button.textContent = label; }, 1500);
    });
  });
})();
