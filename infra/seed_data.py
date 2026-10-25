"""Données de test FICTIVES : 3 agences, 4 services chacune, guichets, et une file de tickets du jour.

Responsable : Mouhamed.
Aucune donnée réelle : noms d'agences inventés, CIN remplacé par une empreinte d'une valeur fictive.
Idempotent : put_item écrase les mêmes clés ; relancer le seed remet la démo à zéro pour la journée.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import common

AGENCIES = [
    {"agency_id": "AG-TUN-03", "name": "Municipalité Tunis-Centre (fictive)", "city": "Tunis",
     "address": "Avenue fictive 1, Tunis"},
    {"agency_id": "AG-ARI-01", "name": "Recette des finances Ariana (fictive)", "city": "Ariana",
     "address": "Rue fictive 12, Ariana"},
    {"agency_id": "AG-SFX-02", "name": "Bureau de poste Sfax-Ville (fictif)", "city": "Sfax",
     "address": "Boulevard fictif 7, Sfax"},
]
SERVICES = [
    {"letter": "A", "code": "LEGALISATION", "name": "Légalisation de signature",
     "required_documents": ["Carte d'identité", "Document à légaliser"], "avg_service_min": 4},
    {"letter": "B", "code": "ETAT_CIVIL", "name": "Extrait d'état civil",
     "required_documents": ["Carte d'identité"], "avg_service_min": 3},
    {"letter": "C", "code": "PAIEMENT", "name": "Paiement de factures et taxes",
     "required_documents": ["Facture ou avis de paiement"], "avg_service_min": 5},
    {"letter": "D", "code": "RECLAMATION", "name": "Réclamations",
     "required_documents": ["Carte d'identité", "Pièces justificatives"], "avg_service_min": 8},
]
COUNTERS = [("C01", ["A", "B"]), ("C02", ["A", "B"]), ("C03", ["C"]), ("C04", ["C", "D"])]
HOURS = "Lun-Ven 08:00-16:00"
WAITING_TICKETS = 8  # pour l'agence de démo AG-TUN-03, service A


def deploy(ctx: dict) -> dict:
    common.require(ctx, "agencies_table", "tickets_table", "control_table")
    ddb = ctx["session"].resource("dynamodb")
    agencies = ddb.Table(ctx["outputs"]["agencies_table"])
    tickets = ddb.Table(ctx["outputs"]["tickets_table"])
    control = ddb.Table(ctx["outputs"]["control_table"])

    with agencies.batch_writer() as batch:
        for agency in AGENCIES:
            batch.put_item(Item={**agency, "item": "META"})
            for service in SERVICES:
                batch.put_item(Item={"agency_id": agency["agency_id"], "item": f"SERVICE#{service['letter']}",
                                     "hours": HOURS, **service})
            for counter_id, letters in COUNTERS:
                batch.put_item(Item={"agency_id": agency["agency_id"], "item": f"COUNTER#{counter_id}",
                                     "counter_id": counter_id, "services": letters, "open": True})

    demo = AGENCIES[0]
    service = SERVICES[0]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=WAITING_TICKETS * 3)
    with tickets.batch_writer() as batch:
        for i in range(1, WAITING_TICKETS + 1):
            number = f"{service['letter']}-{i:03d}"
            created = (start + timedelta(minutes=3 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            batch.put_item(Item={
                "ticket_id": f"{demo['agency_id']}#{today}#{number}",
                "agency_id": demo["agency_id"], "agency_name": demo["name"],
                "service": service["code"], "number": number, "status": "WAITING", "priority": "NORMAL",
                "queue_key": f"{demo['agency_id']}#{today}#{service['letter']}",
                "waiting_since": f"{created}#{number}", "created_at": created,
                "cin_hash": hashlib.sha256(f"fictif-seed-{i}".encode()).hexdigest(),  # jamais un vrai CIN
            })
    control.put_item(Item={"pk": f"SEQ#{demo['agency_id']}#{today}#{service['letter']}",
                           "last_number": WAITING_TICKETS})

    common.log("seed", f"{len(AGENCIES)} agences, {len(SERVICES)} services chacune, "
                       f"{WAITING_TICKETS} tickets en attente pour {demo['agency_id']} ({today})")
    return {"seed_date": today, "demo_agency": demo["agency_id"]}
