(function () {
  "use strict";

  var container = document.getElementById("graph-canvas");
  if (!container) return;

  var endpoint = container.getAttribute("data-endpoint");
  var centerRaw = container.getAttribute("data-center");
  var centerId = centerRaw ? parseInt(centerRaw, 10) : null;

  var hopsSelect = document.getElementById("hops-select");
  if (hopsSelect) {
    hopsSelect.addEventListener("change", function () {
      var url = new URL(window.location.href);
      url.searchParams.set("hops", hopsSelect.value);
      window.location.href = url.toString();
    });
  }

  fetch(endpoint)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var elements = [];

      data.nodes.forEach(function (n) {
        elements.push({
          data: { id: "n" + n.id, label: n.label, ghost: n.ghost, isCenter: n.id === centerId },
        });
      });

      data.edges.forEach(function (e, i) {
        elements.push({
          data: {
            id: "e" + i,
            source: "n" + e.from,
            target: "n" + e.to,
            broken: e.broken,
          },
        });
      });

      var cy = cytoscape({
        container: container,
        elements: elements,
        style: [
          {
            selector: "node",
            style: {
              shape: "rectangle",
              "background-color": "#fbfaf6",
              "border-width": 1.5,
              "border-color": "#26415c",
              label: "data(label)",
              color: "#20241f",
              "font-family": "IBM Plex Mono, monospace",
              "font-size": 10,
              "text-valign": "center",
              "text-halign": "center",
              "text-wrap": "wrap",
              "text-max-width": "110px",
              padding: "8px",
              width: "label",
              height: "label",
            },
          },
          {
            selector: "node[?ghost]",
            style: {
              "border-color": "#a83b2d",
              "border-style": "dashed",
              color: "#a83b2d",
            },
          },
          {
            selector: "node[?isCenter]",
            style: {
              "border-width": 3,
              "background-color": "#f1e6c8",
            },
          },
          {
            selector: "edge",
            style: {
              width: 1.4,
              "line-color": "#7c93a6",
              "target-arrow-color": "#7c93a6",
              "target-arrow-shape": "triangle",
              "arrow-scale": 0.9,
              "curve-style": "bezier",
            },
          },
          {
            selector: "edge[?broken]",
            style: {
              "line-color": "#a83b2d",
              "target-arrow-color": "#a83b2d",
              "line-style": "dashed",
            },
          },
        ],
        layout: { name: "cose", animate: false, padding: 30 },
        minZoom: 0.2,
        maxZoom: 3,
      });

      cy.on("tap", "node", function (evt) {
        var d = evt.target.data();
        if (d.ghost) return;
        var id = d.id.slice(1);
        window.location.href = "/notes/" + id;
      });

      cy.on("mouseover", "node[!ghost]", function (evt) {
        container.style.cursor = "pointer";
      });
      cy.on("mouseout", "node", function () {
        container.style.cursor = "default";
      });

      if (elements.length === 0) {
        var empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "Nothing to show yet -- link notes together with [[note-number]] to see them here.";
        container.replaceWith(empty);
      }
    });
})();
