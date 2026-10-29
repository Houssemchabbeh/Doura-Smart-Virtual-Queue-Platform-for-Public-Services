"""Lambda queue : un agent appelle le prochain ticket à son guichet.

Responsable : Houssem.
Route (protégée par Cognito) :
    POST /agencies/{agency_id}/counters/{counter_id}/call-next   AGENT de l'agence   -> 200 (ticket appelé) ou 204 (file vide)
Principe : pour chaque service du guichet, le plus ancien ticket WAITING du jour (index WaitingQueue) ;
on prend le plus ancien de tous (FIFO entre services), puis écriture conditionnelle WAITING -> CALLED.
Deux agents qui appellent en même temps ne peuvent pas obtenir le même ticket : le perdant passe au candidat suivant.
"""
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import doura_common as dc
import doura_validation as dv

DYNAMODB = boto3.resource("dynamodb")
TICKETS = DYNAMODB.Table(os.environ["TICKETS_TABLE"])
AGENCIES = DYNAMODB.Table(os.environ["AGENCIES_TABLE"])

MAX_ATTEMPTS = 3
PUBLIC_FIELDS = ("ticket_id", "agency_id", "agency_name", "service", "number", "status", "priority",
                 "created_at", "called_at", "counter_id", "updated_at")


def _candidates(agency_id: str, letters: list) -> list:
    """Les plus anciens tickets en attente aujourd'hui pour les services du guichet, du plus ancien au plus récent."""
    day, _ = dc.today()
    found = []
    for letter in letters:
        page = TICKETS.query(IndexName="WaitingQueue", Limit=MAX_ATTEMPTS, ScanIndexForward=True,
                             KeyConditionExpression=Key("queue_key").eq(f"{agency_id}#{day}#{letter}"))
        found.extend(page.get("Items", []))
    return sorted(found, key=lambda t: t["waiting_since"])  # waiting_since = "<iso>#<numéro>" : ordre d'arrivée


def call_next(event):
    params = dv.path_params(event, {"agency_id": dv.agency_id, "counter_id": dv.counter_id})
    agency_id, counter_id = params["agency_id"], params["counter_id"]
    user = dc.require_role(event, "AGENT")
    dc.require_agency(user, agency_id)

    counter = AGENCIES.get_item(Key={"agency_id": agency_id, "item": f"COUNTER#{counter_id}"}).get("Item")
    if not counter:
        raise dc.ApiError(404, "COUNTER_NOT_FOUND", f"Guichet {counter_id} introuvable dans l'agence {agency_id}.")
    if not counter.get("open"):
        raise dc.ApiError(409, "INVALID_TRANSITION", f"Le guichet {counter_id} est fermé.")

    candidates = _candidates(agency_id, counter.get("services", []))
    if not candidates:
        dc.log("queue_empty", agency_id=agency_id, counter_id=counter_id)
        return dc.response(204)

    for ticket in candidates[:MAX_ATTEMPTS]:
        now = dc.now_iso()
        try:
            called = TICKETS.update_item(
                Key={"ticket_id": ticket["ticket_id"]},
                UpdateExpression="SET #s = :called, counter_id = :c, called_at = :now, updated_at = :now "
                                 "REMOVE waiting_since",
                ConditionExpression="#s = :waiting",  # l'index peut être en retard : seule la table fait foi
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":called": "CALLED", ":waiting": "WAITING", ":c": counter_id, ":now": now},
                ReturnValues="ALL_NEW")["Attributes"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                dc.log("call_conflict", ticket_id=ticket["ticket_id"], counter_id=counter_id)
                continue
            raise
        dc.log("ticket_called", ticket_id=called["ticket_id"], counter_id=counter_id, by=user.get("sub"))
        return dc.response(200, {k: called[k] for k in PUBLIC_FIELDS if k in called})

    raise dc.ApiError(409, "CALL_CONFLICT_RETRY", "Tickets appelés simultanément par d'autres guichets : réessayez.")


ROUTES = {("/agencies/{agency_id}/counters/{counter_id}/call-next", "POST"): call_next}


def lambda_handler(event, context):
    return dc.dispatch(event, context, ROUTES)
