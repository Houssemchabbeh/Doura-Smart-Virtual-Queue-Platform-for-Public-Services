"""Smoke test : vérifie que la plateforme déployée répond.

Responsable : Houssem Eddine.
Contrôles : GET /health = 200, GET /agencies renvoie au moins 3 agences, GET /agencies/<agence> = 200,
GET /display = 200, GET /me et POST /tickets sans jeton = 401 (l'autorisateur Cognito bloque bien),
site S3 accessible.
"""
import json
import time
import urllib.error
import urllib.request

import common

RETRIES = 5  # une API tout juste déployée peut mettre quelques secondes à répondre


def run(ctx: dict) -> dict:
    common.require(ctx, "api_url")
    api = ctx["outputs"]["api_url"]
    agency = ctx["outputs"].get("demo_agency", "AG-TUN-03")

    status, body = _get(f"{api}/health")
    _check(status == 200, f"/health a renvoyé {status}")
    common.log("smoke", "/health OK")

    status, body = _get(f"{api}/agencies")
    count = len(json.loads(body).get("agencies", [])) if status == 200 else 0
    _check(status == 200 and count >= 3, f"/agencies : statut {status}, {count} agences")
    common.log("smoke", f"/agencies OK ({count} agences)")

    status, body = _get(f"{api}/agencies/{agency}")
    _check(status == 200 and json.loads(body).get("agency_id") == agency, f"/agencies/{agency} a renvoyé {status}")
    common.log("smoke", f"/agencies/{agency} OK")

    status, _ = _get(f"{api}/display?agency_id={agency}")
    _check(status == 200, f"/display a renvoyé {status}")
    common.log("smoke", "/display OK")

    status, _ = _get(f"{api}/me")
    _check(status == 401, f"/me sans jeton aurait dû renvoyer 401, reçu {status}")
    common.log("smoke", "/me sans jeton -> 401 OK (Cognito actif)")

    status, _ = _get(f"{api}/tickets", method="POST", data=b'{}')
    _check(status == 401, f"POST /tickets sans jeton aurait dû renvoyer 401, reçu {status}")
    common.log("smoke", "POST /tickets sans jeton -> 401 OK")

    if "site_url" in ctx["outputs"]:
        status, _ = _get(ctx["outputs"]["site_url"], retries=2)
        if status == 200:
            common.log("smoke", "site S3 OK")
        else:
            common.log("smoke", f"ATTENTION : site S3 a renvoyé {status} (vérifier le blocage d'accès public)")
    return {"smoke": "ok"}


def _get(url: str, retries: int = RETRIES, method: str = "GET", data: bytes = None):
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, data=data, method=method,
                                             headers={"Content-Type": "application/json"} if data else {})
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503, 504):
                return exc.code, exc.read().decode("utf-8", "replace")
            last = exc.code
        except urllib.error.URLError as exc:
            last = str(exc.reason)
        time.sleep(2 * (attempt + 1))
    return last, ""


def _check(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(f"smoke test : {message}")
