// Écran d'affichage public : interroge GET /display toutes les N secondes.
// Responsable : Adem.
(function () {
  "use strict";

  var cfg = window.DOURA_CONFIG;
  var statusEl = document.getElementById("status");
  var params = new URLSearchParams(window.location.search);
  var lastShown = null;

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.className = isError ? "footer error" : "footer";
  }

  function tick() {
    var now = new Date();
    document.getElementById("clock").textContent =
      String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }

  function timeOf(iso) {
    if (!iso) { return ""; }
    var d = new Date(iso);
    return isNaN(d) ? "" : String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function render(data) {
    document.getElementById("agency-name").textContent = data.agency_name || data.agency_id;
    var called = data.called || [];
    var current = called[0];
    var numberEl = document.getElementById("current-number");
    numberEl.textContent = current ? current.number : "—";
    document.getElementById("current-counter").textContent = current ? (current.counter_id || "—") : "—";

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
    called.slice(1).forEach(function (t) {
      var tr = document.createElement("tr");
      [t.number, t.service, t.counter_id || "—", timeOf(t.called_at)].forEach(function (v) {
        var td = document.createElement("td");
        td.textContent = v;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function refresh(agency) {
    fetch(cfg.apiUrl + "/display?agency_id=" + encodeURIComponent(agency))
      .then(function (r) {
        return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
      })
      .then(function (res) {
        if (!res.ok) {
          var msg = res.body && res.body.error ? res.body.error.message : "HTTP " + res.status;
          throw new Error(msg);
        }
        render(res.body);
        setStatus("Mis à jour à " + timeOf(res.body.generated_at) + " · actualisation toutes les " + cfg.refreshSeconds + " s", false);
      })
      .catch(function (err) {
        setStatus("Connexion perdue : " + err.message + " · nouvel essai dans " + cfg.refreshSeconds + " s", true);
      });
  }

  tick();
  setInterval(tick, 10000);

  if (!cfg) {
    setStatus("config.js introuvable : lancer infra/deploy.py.", true);
    return;
  }
  var agency = params.get("agency") || cfg.defaultAgency;
  refresh(agency);
  setInterval(function () { refresh(agency); }, (cfg.refreshSeconds || 5) * 1000);
})();
