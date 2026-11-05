// Écran d'affichage public : interroge GET /display toutes les N secondes (5 par défaut).
// Page publique : aucune authentification. Mode démo hors ligne : ?demo=1 (voir demo.js).
// Responsable : Adem.
(function () {
  "use strict";

  var cfg = window.DOURA_CONFIG;
  var statusEl = document.getElementById("status");
  var progressEl = document.getElementById("refresh-progress");
  var params = new URLSearchParams(window.location.search);
  var demoMode = params.get("demo") === "1" || !cfg;
  var refreshSeconds = (cfg && cfg.refreshSeconds) || 5;
  var LIMIT = 5;  // les 5 derniers tickets appelés
  var lastShown = null;

  // Codes de service (infra/seed_data.py) -> libellés lisibles pour le public.
  var SERVICE_NAMES = {
    LEGALISATION: "Légalisation de signature",
    ETAT_CIVIL: "Extrait d'état civil",
    PAIEMENT: "Paiement de factures et taxes",
    RECLAMATION: "Réclamations"
  };

  function pad(n) { return String(n).padStart(2, "0"); }

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.className = isError ? "status-text error" : "status-text";
  }

  function tick() {
    var now = new Date();
    document.getElementById("clock").textContent = pad(now.getHours()) + ":" + pad(now.getMinutes());
  }

  function timeOf(iso, withSeconds) {
    if (!iso) { return ""; }
    var d = new Date(iso);
    if (isNaN(d)) { return ""; }
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + (withSeconds ? ":" + pad(d.getSeconds()) : "");
  }

  // Barre de progression : se vide en refreshSeconds, relancée à chaque actualisation.
  function restartProgress() {
    progressEl.style.animation = "none";
    void progressEl.offsetWidth;
    progressEl.style.animation = "countdown " + refreshSeconds + "s linear forwards";
  }

  function render(data) {
    document.getElementById("agency-name").textContent = data.agency_name || data.agency_id;
    var called = (data.called || []).slice(0, LIMIT);
    var current = called[0];
    var numberEl = document.getElementById("current-number");
    numberEl.textContent = current ? current.number : "—";
    document.getElementById("current-counter").textContent = current ? (current.counter_id || "—") : "—";
    document.getElementById("current-service").textContent = current ? (SERVICE_NAMES[current.service] || current.service || "") : "";

    // Nouvel appel : faire clignoter le numéro pour attirer l'attention.
    var key = current ? current.number + "|" + current.called_at : null;
    if (key && key !== lastShown) {
      numberEl.classList.remove("flash");
      void numberEl.offsetWidth;  // relance l'animation
      numberEl.classList.add("flash");
      lastShown = key;
    }

    var tbody = document.getElementById("history");
    tbody.textContent = "";
    if (called.length <= 1) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 4;
      td.className = "empty";
      td.textContent = "Aucun appel précédent.";
      empty.appendChild(td);
      tbody.appendChild(empty);
    }
    called.slice(1).forEach(function (t) {
      var tr = document.createElement("tr");
      [t.number, SERVICE_NAMES[t.service] || t.service || "", t.counter_id || "—", timeOf(t.called_at)].forEach(function (v) {
        var cell = document.createElement("td");
        cell.textContent = v;
        tr.appendChild(cell);
      });
      tbody.appendChild(tr);
    });
  }

  function fetchApi(agency) {
    return fetch(cfg.apiUrl + "/display?agency_id=" + encodeURIComponent(agency))
      .then(function (r) {
        return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
      })
      .then(function (res) {
        if (!res.ok) {
          throw new Error(res.body && res.body.error ? res.body.error.message : "HTTP " + res.status);
        }
        return res.body;
      });
  }

  function refresh(agency) {
    restartProgress();
    var source = demoMode ? window.DOURA_DEMO.fetchDisplay(agency) : fetchApi(agency);
    source
      .then(function (body) {
        render(body);
        setStatus("Mis à jour à " + timeOf(body.generated_at, true) + " · actualisation toutes les " + refreshSeconds + " s", false);
      })
      .catch(function (err) {
        setStatus("Connexion perdue : " + err.message + " · nouvel essai dans " + refreshSeconds + " s", true);
      });
  }

  tick();
  setInterval(tick, 10000);

  if (demoMode) { document.getElementById("demo-badge").hidden = false; }
  var agency = params.get("agency") || (cfg && cfg.defaultAgency) || "AG-TUN-03";
  refresh(agency);
  setInterval(function () { refresh(agency); }, refreshSeconds * 1000);
})();
