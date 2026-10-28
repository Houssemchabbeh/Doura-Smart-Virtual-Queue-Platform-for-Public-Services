"""Lambda health : GET /health (public) et GET /me (protégé par Cognito).

Responsable : Houssem (sert à valider l'API et l'autorisateur Cognito).
/health -> état de la plateforme, utilisé par le smoke test.
/me     -> identité de l'utilisateur connecté (email, groupes, agence), prouve que Cognito fonctionne.
"""
import os

import doura_common as dc


def lambda_handler(event, context):
    path = event.get("resource") or event.get("path") or "/health"

    if path == "/me":
        user = dc.claims(event)
        if not user:
            return dc.error(401, "UNAUTHORIZED", "Jeton manquant ou invalide.", dc.request_id(context))
        return dc.response(200, {
            "email": user.get("email"),
            "groups": dc.groups(event),
            "agency_id": user.get("custom:agency_id"),
        })

    return dc.response(200, {"status": "ok", "service": "doura", "stage": os.environ.get("STAGE"), "time": dc.now_iso()})
