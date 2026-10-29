"""Module partagé par toutes les Lambdas Doura (copié dans chaque zip au déploiement).

Responsable : Mouhamed.
Contenu : réponses HTTP avec CORS, format d'erreur unique, journalisation JSON sans CIN,
lecture des claims Cognito, contrôle des rôles et aiguillage des routes (Livrable 3).
"""
import json
import logging
import traceback
from datetime import datetime, timezone
from decimal import Decimal

import doura_validation as dv

logger = logging.getLogger("doura")
logger.setLevel(logging.INFO)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
}

# Champs jamais journalisés, même par erreur.
_SENSITIVE_KEYS = ("cin", "cin_hash", "password", "token", "authorization")


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Type non sérialisable : {type(value).__name__}")


def response(status: int, body=None) -> dict:
    """Réponse au format Lambda proxy d'API Gateway."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8", **CORS_HEADERS},
        "body": "" if body is None else json.dumps(body, default=_json_default, ensure_ascii=False),
    }


def error(status: int, code: str, message: str, request_id: str = None) -> dict:
    """Format d'erreur unique défini dans le contrat d'API."""
    return response(status, {"error": {"code": code, "message": message, "request_id": request_id}})


def mask(data):
    """Copie d'un dict/list avec les champs sensibles masqués."""
    if isinstance(data, dict):
        return {k: ("***" if any(s in k.lower() for s in _SENSITIVE_KEYS) else mask(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [mask(v) for v in data]
    return data


def log(event: str, **fields) -> None:
    """Journal JSON structuré, lisible dans CloudWatch Logs Insights."""
    logger.info(json.dumps({"event": event, **mask(fields)}, default=_json_default, ensure_ascii=False))


def claims(event: dict) -> dict:
    """Claims Cognito ajoutés par l'autorisateur d'API Gateway (vide si route publique)."""
    return (event.get("requestContext") or {}).get("authorizer", {}).get("claims", {}) or {}


def groups(event: dict) -> list:
    raw = claims(event).get("cognito:groups", "")
    if isinstance(raw, list):
        return raw
    return [g for g in str(raw).replace("[", "").replace("]", "").replace(",", " ").split() if g]


def query_param(event: dict, name: str, default=None):
    return (event.get("queryStringParameters") or {}).get(name, default)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request_id(context) -> str:
    return getattr(context, "aws_request_id", None)


def today() -> tuple:
    """Date du jour (UTC) : ('2026-10-26', '20261026') -> clés de file et identifiant de ticket."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d")


class ApiError(Exception):
    """Erreur métier convertie en réponse HTTP au format unique."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def require_role(event: dict, *roles: str) -> dict:
    """Vérifie l'authentification (401) puis le groupe Cognito (403) ; renvoie les claims."""
    user = claims(event)
    if not user.get("sub"):
        raise ApiError(401, "UNAUTHORIZED", "Jeton manquant ou invalide.")
    if not set(roles) & set(groups(event)):
        raise ApiError(403, "FORBIDDEN", "Action réservée au rôle : " + " ou ".join(roles) + ".")
    return user


def require_agency(user: dict, agency_id: str) -> None:
    """Un agent ou responsable n'agit que sur sa propre agence (claim custom:agency_id)."""
    if user.get("custom:agency_id") != agency_id:
        raise ApiError(403, "WRONG_AGENCY", "Vous n'êtes pas rattaché à cette agence.")


def dispatch(event: dict, context, routes: dict) -> dict:
    """Aiguille (resource, méthode) vers sa fonction et convertit toute erreur au format unique.

    Erreur inattendue -> 500 INTERNAL_ERROR avec message générique ; le détail ne va que dans les logs.
    """
    rid = request_id(context)
    route = (event.get("resource"), event.get("httpMethod"))
    try:
        func = routes.get(route)
        if func is None:
            raise ApiError(405, "METHOD_NOT_ALLOWED", f"Méthode {route[1]} non autorisée sur {route[0]}.")
        return func(event)
    except ApiError as exc:
        return error(exc.status, exc.code, exc.message, rid)
    except dv.InvalidJson:
        return error(400, "INVALID_JSON", "Le corps de la requête doit être un objet JSON valide.", rid)
    except dv.ValidationError as exc:
        return error(400, "VALIDATION_ERROR", exc.message, rid)
    except Exception as exc:  # noqa: BLE001 - dernier filet de sécurité
        log("unhandled_error", route=f"{route[1]} {route[0]}", error_type=type(exc).__name__,
            detail=str(exc)[:500], trace=traceback.format_exc(limit=5), request_id=rid)
        return error(500, "INTERNAL_ERROR", "Erreur interne. Réessayez plus tard.", rid)
