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

      // Too many notes at this distance: say what's shown (db.get_graph_data).
      var capped = document.getElementById("graph-capped");
      if (capped && data.left_out) {
        capped.textContent = "Showing the nearest " + data.nodes.length + " notes; " +
          data.left_out + " more at that distance (and any beyond) aren’t shown." +
          (hopsSelect && +hopsSelect.value > 1 ? " Fewer hops shows a complete graph." : "");
        capped.hidden = false;
      }

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
              "font-size": 12,
              // Labels too small to read are hidden rather than drawn as
              // smudges; they appear as you zoom in (hover shows them too).
              "min-zoomed-font-size": 8,
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
        // Force-directed layout tuned for labeled boxes rather than dots:
        // node size includes the label, and repulsion/edge length are large
        // enough that boxes don't stack on top of each other.
        layout: {
          name: "cose",
          animate: false,
          padding: 40,
          nodeDimensionsIncludeLabels: true,
          idealEdgeLength: function () { return 120; },
          nodeRepulsion: function () { return 900000; },
          nodeOverlap: 40,
          gravity: 0.3,
          componentSpacing: 120,
          numIter: 2500,
        },
        minZoom: 0.2,
        maxZoom: 3,
      });

      // A small graph shouldn't be blown up to fill the window.
      if (cy.zoom() > 1.3) {
        cy.zoom(1.3);
        cy.center();
      }

      cy.on("tap", "node", function (evt) {
        var d = evt.target.data();
        if (d.ghost) return;
        var id = d.id.slice(1);
        window.location.href = "/notes/" + id;
      });

      // Hover tooltip with the node's full label, so notes can be
      // identified from the zoomed-out overview where labels are hidden.
      var tip = document.createElement("div");
      tip.className = "graph-tip";
      tip.hidden = true;
      document.body.appendChild(tip);

      function placeTip(evt) {
        if (!evt.originalEvent) return;
        tip.style.left = evt.originalEvent.clientX + 14 + "px";
        tip.style.top = evt.originalEvent.clientY + 14 + "px";
      }

      cy.on("mouseover", "node", function (evt) {
        var d = evt.target.data();
        container.style.cursor = d.ghost ? "default" : "pointer";
        tip.textContent = d.label;
        tip.classList.toggle("ghost", !!d.ghost);
        placeTip(evt); // position before showing, not only on the next move
        tip.hidden = false;
      });
      cy.on("mousemove", function (evt) {
        if (!tip.hidden) placeTip(evt);
      });
      cy.on("mouseout", "node", function () {
        container.style.cursor = "default";
        tip.hidden = true;
      });
      cy.on("zoom pan", function () { tip.hidden = true; });

      if (elements.length === 0) {
        var empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "Nothing to show yet -- link notes together with [[note-number]] to see them here.";
        container.replaceWith(empty);
      }
    });
})();
