// Mode démo de l'écran d'affichage : données FICTIVES générées dans le navigateur, sans backend.
// Activé par ?demo=1 ou automatiquement si config.js est absent.
// Renvoie la même forme de réponse que GET /display (backend/functions/display/handler.py).
(function () {
  "use strict";

  // Services et guichets repris de infra/seed_data.py.
  var SERVICES = [
    { letter: "A", code: "LEGALISATION", counters: ["C01", "C02"] },
    { letter: "B", code: "ETAT_CIVIL", counters: ["C01", "C02"] },
    { letter: "C", code: "PAIEMENT", counters: ["C03", "C04"] },
    { letter: "D", code: "RECLAMATION", counters: ["C04"] }
  ];
  var LIMIT = 5;           // comme LIMIT dans la Lambda display
  var CALL_EVERY = 2;      // un nouvel appel toutes les 2 actualisations

  var nextNumber = { A: 1, B: 1, C: 1, D: 1 };
  var called = [];          // le plus récent en premier
  var refreshes = 0;

  function pick(list) { return list[Math.floor(Math.random() * list.length)]; }

  function callTicket(at) {
    var s = pick(SERVICES);
    var n = nextNumber[s.letter]++;
    called.unshift({
      number: s.letter + "-" + String(n).padStart(3, "0"),
      service: s.code,
      counter_id: pick(s.counters),
      status: "CALLED",
      called_at: at.toISOString()
    });
    called = called.slice(0, LIMIT);
  }

  // Historique de départ : 5 appels sur les dernières minutes.
  var now = Date.now();
  for (var i = LIMIT; i >= 1; i--) { callTicket(new Date(now - i * 90 * 1000)); }

  window.DOURA_DEMO = {
    fetchDisplay: function (agencyId) {
      refreshes++;
      if (refreshes > 1 && refreshes % CALL_EVERY === 0) { callTicket(new Date()); }
      return Promise.resolve({
        agency_id: agencyId,
        agency_name: "Municipalité Tunis-Centre (fictive)",
        called: called.slice(),
        generated_at: new Date().toISOString()
      });
    }
  };
})();
