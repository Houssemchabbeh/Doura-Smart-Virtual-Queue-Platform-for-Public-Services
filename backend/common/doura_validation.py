"""Validation des entrées de l'API Doura (fonctions pures, sans boto3), partagée par toutes les Lambdas.

Responsable : Houssem.
Usage dans un handler :
    body = dv.parse_body(event)                                   # InvalidJson -> 400 INVALID_JSON
    data = dv.validate(body, {"name": dv.name, ...}, required=("name",))  # ValidationError -> 400 VALIDATION_ERROR
Chaque validateur renvoie la valeur nettoyée (ex. texte sans espaces autour) ou lève ValueError.
"""
import base64
import json
import re


class InvalidJson(Exception):
    """Le corps de la requête n'est pas un objet JSON."""


class ValidationError(Exception):
    """Un ou plusieurs champs invalides ; `fields` liste leurs noms."""

    def __init__(self, fields):
        self.fields = sorted(set(fields))
        super().__init__(self.message)

    @property
    def message(self) -> str:
        return "Champs invalides ou manquants : " + ", ".join(self.fields) + "."


# --- Formats ----------------------------------------------------------------------------------------------

AGENCY_ID = re.compile(r"^AG-[A-Z]{3}-\d{2}$")
LETTER = re.compile(r"^[A-Z]$")
CODE = re.compile(r"^[A-Z_]{3,30}$")
CIN = re.compile(r"^\d{8}$")
TICKET_ID = re.compile(r"^AG-[A-Z]{3}-\d{2}_\d{8}_[A-Z]-\d{3}$")
COUNTER_ID = re.compile(r"^C\d{2}$")
FINAL_STATUSES = ("SERVED", "NO_SHOW")


def _pattern(regex):
    def check(value):
        if not isinstance(value, str) or not regex.match(value):
            raise ValueError
        return value
    return check


def _text(min_len: int, max_len: int):
    def check(value):
        if not isinstance(value, str):
            raise ValueError
        value = value.strip()
        if not min_len <= len(value) <= max_len:
            raise ValueError
        return value
    return check


agency_id = _pattern(AGENCY_ID)
letter = _pattern(LETTER)
code = _pattern(CODE)
cin = _pattern(CIN)
ticket_id = _pattern(TICKET_ID)
counter_id = _pattern(COUNTER_ID)
name = _text(3, 80)
hours = _text(5, 60)


def required_documents(value):
    """Liste de 0 à 10 textes de 2 à 80 caractères."""
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError
    return [_text(2, 80)(item) for item in value]


def avg_service_min(value):
    """Entier de 1 à 120 (un booléen n'est pas accepté, bien que True soit un int en Python)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 120:
        raise ValueError
    return value


def final_status(value):
    if value not in FINAL_STATUSES:
        raise ValueError
    return value


# --- Corps et ensembles de champs ---------------------------------------------------------------------------

def parse_body(event: dict) -> dict:
    """Corps JSON de la requête ; doit être un objet. Corps absent -> objet vide."""
    raw = event.get("body")
    if raw in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidJson from exc
    try:
        body = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidJson from exc
    if not isinstance(body, dict):
        raise InvalidJson
    return body


def validate(data: dict, schema: dict, required=(), at_least_one: bool = False) -> dict:
    """Valide `data` selon `schema` {champ: validateur}.

    - champ inconnu -> invalide ; champ obligatoire absent -> invalide ;
    - at_least_one : au moins un champ du schéma doit être fourni (mise à jour partielle).
    Renvoie un dict des valeurs nettoyées ; lève ValidationError avec la liste des champs fautifs.
    """
    data = data or {}
    bad = [key for key in data if key not in schema]
    bad += [key for key in required if key not in data]
    cleaned = {}
    for key, check in schema.items():
        if key not in data:
            continue
        try:
            cleaned[key] = check(data[key])
        except (ValueError, TypeError):
            bad.append(key)
    if at_least_one and not data:
        bad.append("body")
    if bad:
        raise ValidationError(bad)
    return cleaned


def path_params(event: dict, schema: dict) -> dict:
    """Valide les paramètres de chemin ({agency_id}, {ticket_id}...) ; tous sont obligatoires."""
    params = event.get("pathParameters") or {}
    return validate({k: params.get(k) for k in schema if k in params}, schema, required=tuple(schema))
