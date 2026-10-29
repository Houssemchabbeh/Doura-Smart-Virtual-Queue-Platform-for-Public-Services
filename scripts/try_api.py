"""Scénario complet de l'API REST déployée (démo et captures du Livrable 3).

Responsable : Houssem.
Connecte les utilisateurs FICTIFS de Cognito (USER_PASSWORD_AUTH), puis enchaîne les appels HTTP réels :
citoyen -> ticket, doublon 409, position ; agent -> appel du suivant, SERVED ; responsable -> CRUD d'un service ;
et les cas d'erreur 400 / 401 / 403 / 404 / 409. Chaque ligne affiche le statut obtenu et le statut attendu.

Usage (depuis la racine du dépôt, identifiants Learner Lab valides) :
    python scripts/try_api.py --stage houssem
Mot de passe des comptes de test : variable DOURA_TEST_PASSWORD, sinon celui par défaut de infra/cognito.py.
Rejouable : le ticket est servi et le service de test supprimé à la fin.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra"))
from cognito import TEST_PASSWORD  # noqa: E402

AGENCY = "AG-TUN-03"
OTHER_AGENCY = "AG-ARI-01"
CIN_1 = "11223344"  # CIN fictifs
CIN_2 = "55667788"
TEST_SERVICE = {"letter": "E", "code": "PERMIS_BATIR", "name": "Permis de bâtir (test)",
                "hours": "Lun-Ven 08:00-12:00", "required_documents": ["Carte d'identité", "Plan du terrain"],
                "avg_service_min": 10}


class Runner:
    def __init__(self, api_url: str):
        self.api = api_url
        self.failures = 0
        self.step = 0

    def request(self, method: str, path: str, body=None, token: str = None, raw: str = None):
        data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = token
        req = urllib.request.Request(self.api + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status, text = resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode("utf-8", "replace")
        return status, (json.loads(text) if text else None)

    def check(self, title: str, expected, method: str, path: str, **kwargs):
        """Exécute un appel, affiche le résultat ; `expected` = statut ou tuple de statuts acceptés."""
        self.step += 1
        status, body = self.request(method, path, **kwargs)
        accepted = expected if isinstance(expected, tuple) else (expected,)
        ok = status in accepted
        self.failures += 0 if ok else 1
        print(f"{'OK' if ok else 'KO'}  {self.step:>2}. {title}")
        print(f"        {method} {path} -> {status} (attendu {'/'.join(map(str, accepted))})")
        if body is not None:
            text = json.dumps(body, ensure_ascii=False)
            print(f"        {text[:220]}{'...' if len(text) > 220 else ''}")
        return status, body


def sign_in(cognito, client_id: str, email: str, password: str) -> str:
    """Jeton d'identité Cognito (contient cognito:groups et custom:agency_id)."""
    result = cognito.initiate_auth(ClientId=client_id, AuthFlow="USER_PASSWORD_AUTH",
                                   AuthParameters={"USERNAME": email, "PASSWORD": password})
    return result["AuthenticationResult"]["IdToken"]


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scénario de démonstration de l'API Doura")
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()

    path = ROOT / "infra" / f"outputs-{args.stage}.json"
    if not path.exists():
        sys.exit(f"{path} introuvable : lancez d'abord python infra/deploy.py --stage {args.stage}")
    outputs = json.loads(path.read_text())
    password = os.environ.get("DOURA_TEST_PASSWORD", TEST_PASSWORD)
    cognito = boto3.Session(region_name="us-east-1").client("cognito-idp")
    tokens = {email: sign_in(cognito, outputs["user_pool_client_id"], email, password) for email in (
        "citoyen1@doura.test", "citoyen2@doura.test", "agent.tunis@doura.test", "agent.ariana@doura.test",
        "manager.tunis@doura.test")}
    citizen, citizen2 = tokens["citoyen1@doura.test"], tokens["citoyen2@doura.test"]
    agent, agent_other = tokens["agent.tunis@doura.test"], tokens["agent.ariana@doura.test"]
    manager = tokens["manager.tunis@doura.test"]
    run = Runner(outputs["api_url"])
    print(f"API : {outputs['api_url']}  |  5 utilisateurs fictifs connectés via Cognito")

    section("Lecture publique")
    run.check("Santé de la plateforme", 200, "GET", "/health")
    run.check("Détail d'une agence", 200, "GET", f"/agencies/{AGENCY}")
    run.check("Services d'une agence", 200, "GET", f"/agencies/{AGENCY}/services")
    run.check("Agence inconnue", 404, "GET", "/agencies/AG-XXX-99")
    run.check("Identifiant d'agence mal formé", 400, "GET", "/agencies/tunis")

    section("Citoyen : prise de ticket (service C, guichet C03)")
    run.check("Prise de ticket sans jeton", 401, "POST", "/tickets", body={"agency_id": AGENCY, "service": "C", "cin": CIN_1})
    status, ticket = run.check("Prise de ticket", 201, "POST", "/tickets", token=citizen,
                               body={"agency_id": AGENCY, "service": "C", "cin": CIN_1})
    run.check("Doublon : même citoyen, même service, même jour", 409, "POST", "/tickets", token=citizen,
              body={"agency_id": AGENCY, "service": "C", "cin": CIN_1})
    run.check("CIN invalide (4 chiffres)", 400, "POST", "/tickets", token=citizen,
              body={"agency_id": AGENCY, "service": "C", "cin": "1234"})
    run.check("Champ inconnu", 400, "POST", "/tickets", token=citizen,
              body={"agency_id": AGENCY, "service": "C", "cin": CIN_1, "priority": "VIP"})
    run.check("JSON invalide", 400, "POST", "/tickets", token=citizen, raw="{pas du json")
    run.check("Service inexistant", 404, "POST", "/tickets", token=citizen,
              body={"agency_id": AGENCY, "service": "Z", "cin": CIN_1})
    run.check("Un agent ne prend pas de ticket", 403, "POST", "/tickets", token=agent,
              body={"agency_id": AGENCY, "service": "C", "cin": CIN_1})
    if status != 201:
        print("\nTicket non créé : la suite du scénario est interrompue.")
        return 1
    ticket_path = f"/tickets/{ticket['ticket_id']}"
    run.check("Position et attente estimée", 200, "GET", ticket_path, token=citizen)
    run.check("Un autre citoyen ne voit pas ce ticket", 403, "GET", ticket_path, token=citizen2)

    section("Agent : appel et clôture")
    run.check("Clôture d'un ticket pas encore appelé", 409, "PATCH", ticket_path, token=agent, body={"status": "SERVED"})
    run.check("Agent d'une autre agence", 403, "POST", f"/agencies/{AGENCY}/counters/C03/call-next", token=agent_other)
    run.check("Guichet inexistant", 404, "POST", f"/agencies/{AGENCY}/counters/C09/call-next", token=agent)
    run.check("Appel du suivant au guichet C03", 200, "POST", f"/agencies/{AGENCY}/counters/C03/call-next", token=agent)
    run.check("Statut final invalide", 400, "PATCH", ticket_path, token=agent, body={"status": "CANCELLED"})
    run.check("Ticket servi (SERVED -> e-mail SNS)", 200, "PATCH", ticket_path, token=agent, body={"status": "SERVED"})
    run.check("Transition interdite SERVED -> NO_SHOW", 409, "PATCH", ticket_path, token=agent, body={"status": "NO_SHOW"})
    run.check("File vide au guichet C03", 204, "POST", f"/agencies/{AGENCY}/counters/C03/call-next", token=agent)

    section("Citoyen : annulation")
    status, other = run.check("Second citoyen prend un ticket (service D)", 201, "POST", "/tickets", token=citizen2,
                              body={"agency_id": AGENCY, "service": "D", "cin": CIN_2})
    if status == 201:
        other_path = f"/tickets/{other['ticket_id']}"
        run.check("Annulation par un autre citoyen", 403, "DELETE", other_path, token=citizen)
        run.check("Annulation par son propriétaire", 200, "DELETE", other_path, token=citizen2)
        run.check("Deuxième annulation", 409, "DELETE", other_path, token=citizen2)

    section("Responsable d'agence : CRUD d'un service")
    services = f"/agencies/{AGENCY}/services"
    run.request("DELETE", f"{services}/E", token=manager)  # nettoyage d'une exécution précédente interrompue
    run.check("Création par un citoyen", 403, "POST", services, token=citizen, body=TEST_SERVICE)
    run.check("Création pour une autre agence", 403, "POST", f"/agencies/{OTHER_AGENCY}/services", token=manager,
              body=TEST_SERVICE)
    run.check("Création avec champs invalides", 400, "POST", services, token=manager,
              body={**TEST_SERVICE, "code": "permis", "avg_service_min": 0})
    run.check("Création du service E", 201, "POST", services, token=manager, body=TEST_SERVICE)
    run.check("Création en double", 409, "POST", services, token=manager, body=TEST_SERVICE)
    run.check("Modification partielle", 200, "PUT", f"{services}/E", token=manager, body={"avg_service_min": 12})
    run.check("Modification sans champ", 400, "PUT", f"{services}/E", token=manager, body={})
    status, waiting = run.check("Un citoyen prend un ticket sur le service E", 201, "POST", "/tickets", token=citizen,
                                body={"agency_id": AGENCY, "service": "E", "cin": CIN_1})
    run.check("Suppression refusée : ticket en attente", 409, "DELETE", f"{services}/E", token=manager)
    if status == 201:
        run.check("Le citoyen annule son ticket", 200, "DELETE", f"/tickets/{waiting['ticket_id']}", token=citizen)
    run.check("Suppression du service E", 204, "DELETE", f"{services}/E", token=manager)
    run.check("Suppression d'un service absent", 404, "DELETE", f"{services}/E", token=manager)

    print(f"\n{run.step} appels, {run.failures} écart(s) par rapport au résultat attendu.")
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())
