"""Lambda display : GET /display?agency_id=AG-TUN-03 (public, utilisée par l'écran de la salle d'attente).

Responsable : Mouhamed.
Renvoie les 5 derniers tickets appelés de l'agence, avec leur guichet.
Index AgencyCalled de la table tickets : PK agency_id, SK called_at (seuls les tickets appelés y figurent).
"""
import os

import boto3
from boto3.dynamodb.conditions import Key

import doura_common as dc

DYNAMODB = boto3.resource("dynamodb")
TICKETS = DYNAMODB.Table(os.environ["TICKETS_TABLE"])
AGENCIES = DYNAMODB.Table(os.environ["AGENCIES_TABLE"])
LIMIT = 5


def lambda_handler(event, context):
    agency_id = dc.query_param(event, "agency_id")
    if not agency_id:
        return dc.error(400, "VALIDATION_ERROR", "Paramètre agency_id obligatoire.", dc.request_id(context))

    agency = AGENCIES.get_item(Key={"agency_id": agency_id, "item": "META"}).get("Item")
    if not agency:
        return dc.error(404, "AGENCY_NOT_FOUND", f"Agence {agency_id} inconnue.", dc.request_id(context))

    page = TICKETS.query(
        IndexName="AgencyCalled",
        KeyConditionExpression=Key("agency_id").eq(agency_id),
        ScanIndexForward=False,  # le plus récent en premier
        Limit=LIMIT,
    )
    called = [{
        "number": t.get("number"),
        "service": t.get("service"),
        "counter_id": t.get("counter_id"),
        "status": t.get("status"),
        "called_at": t.get("called_at"),
    } for t in page.get("Items", [])]

    return dc.response(200, {
        "agency_id": agency_id,
        "agency_name": agency.get("name"),
        "called": called,
        "generated_at": dc.now_iso(),
    })
