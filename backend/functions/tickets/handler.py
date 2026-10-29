"""Lambda tickets : prise de ticket, suivi, annulation et clôture.

Responsable : Houssem.
Routes (toutes protégées par Cognito) :
    POST   /tickets              CITIZEN                                  prendre un ticket        -> 201
    GET    /tickets/{ticket_id}  CITIZEN propriétaire, AGENT/MANAGER agence  position et attente   -> 200
    DELETE /tickets/{ticket_id}  CITIZEN propriétaire                     annuler (CANCELLED)      -> 200
    PATCH  /tickets/{ticket_id}  AGENT de l'agence                        SERVED ou NO_SHOW        -> 200
Tables : tickets (PK ticket_id, index WaitingQueue), agencies, control (SEQ# compteurs, ACTIVE# verrous).
Le CIN n'est jamais stocké ni journalisé : seule son empreinte HMAC-SHA256 (sel CIN_SALT) est conservée.
"""
import hashlib
import hmac
import math
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import doura_common as dc
import doura_validation as dv

DYNAMODB = boto3.resource("dynamodb")
TICKETS = DYNAMODB.Table(os.environ["TICKETS_TABLE"])
AGENCIES = DYNAMODB.Table(os.environ["AGENCIES_TABLE"])
CONTROL = DYNAMODB.Table(os.environ["CONTROL_TABLE"])
CIN_SALT = os.environ["CIN_SALT"].encode()

# Champs renvoyés au client : jamais cin_hash, owner_sub, queue_key.
PUBLIC_FIELDS = ("ticket_id", "agency_id", "agency_name", "service", "number", "status", "priority", "created_at",
                 "called_at", "counter_id", "served_at", "no_show_at", "cancelled_at", "updated_at")
STAFF = ("AGENT", "MANAGER")


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _hash_cin(cin: str) -> str:
    return hmac.new(CIN_SALT, cin.encode(), hashlib.sha256).hexdigest()


def _letter(ticket: dict) -> str:
    return ticket["number"].split("-")[0]


def _lock_key(cin_hash: str, queue_key: str) -> dict:
    """Verrou « un seul ticket actif par citoyen, agence, jour et service »."""
    return {"pk": f"ACTIVE#{cin_hash}#{queue_key}"}


def _public(ticket: dict) -> dict:
    return {k: ticket[k] for k in PUBLIC_FIELDS if k in ticket}


def _with_position(ticket: dict) -> dict:
    """Ajoute position (1 = prochain) et estimated_wait_min au ticket public."""
    result = _public(ticket)
    position = 0
    if ticket.get("status") == "WAITING" and ticket.get("waiting_since"):
        position = 1
        kwargs = {"IndexName": "WaitingQueue", "Select": "COUNT",
                  "KeyConditionExpression": Key("queue_key").eq(ticket["queue_key"])
                  & Key("waiting_since").lt(ticket["waiting_since"])}
        while True:
            page = TICKETS.query(**kwargs)
            position += page.get("Count", 0)
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    estimated = 0
    if position:
        letter = _letter(ticket)
        service = AGENCIES.get_item(Key={"agency_id": ticket["agency_id"], "item": f"SERVICE#{letter}"}).get("Item") or {}
        counters = AGENCIES.query(KeyConditionExpression=Key("agency_id").eq(ticket["agency_id"])
                                  & Key("item").begins_with("COUNTER#")).get("Items", [])
        open_counters = sum(1 for c in counters if c.get("open") and letter in c.get("services", []))
        estimated = math.ceil(position * int(service.get("avg_service_min", 5)) / max(1, open_counters))
    result.update({"position": position, "estimated_wait_min": estimated})
    return result


def _load(ticket_id: str) -> dict:
    ticket = TICKETS.get_item(Key={"ticket_id": ticket_id}).get("Item")
    if not ticket:
        raise dc.ApiError(404, "TICKET_NOT_FOUND", f"Ticket {ticket_id} introuvable.")
    return ticket


def _release_lock(ticket: dict) -> None:
    if ticket.get("cin_hash") and ticket.get("queue_key"):
        CONTROL.delete_item(Key=_lock_key(ticket["cin_hash"], ticket["queue_key"]))


# --- Routes --------------------------------------------------------------------------------------------------

def create_ticket(event):
    data = dv.validate(dv.parse_body(event), {"agency_id": dv.agency_id, "service": dv.letter, "cin": dv.cin},
                       required=("agency_id", "service", "cin"))
    user = dc.require_role(event, "CITIZEN")
    agency_id, letter = data["agency_id"], data["service"]

    agency = AGENCIES.get_item(Key={"agency_id": agency_id, "item": "META"}).get("Item")
    if not agency:
        raise dc.ApiError(404, "AGENCY_NOT_FOUND", f"Agence {agency_id} introuvable.")
    service = AGENCIES.get_item(Key={"agency_id": agency_id, "item": f"SERVICE#{letter}"}).get("Item")
    if not service:
        raise dc.ApiError(404, "SERVICE_NOT_FOUND", f"Service {letter} introuvable dans l'agence {agency_id}.")

    cin_hash = _hash_cin(data.pop("cin"))  # le CIN en clair n'est plus utilisé après cette ligne
    day, day_compact = dc.today()
    queue_key = f"{agency_id}#{day}#{letter}"
    now = dc.now_iso()

    # 1. Verrou : refus AVANT de consommer un numéro.
    try:
        CONTROL.put_item(Item={**_lock_key(cin_hash, queue_key), "created_at": now},
                         ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if _conditional_failed(exc):
            raise dc.ApiError(409, "TICKET_ALREADY_ACTIVE",
                              "Vous avez déjà un ticket actif pour ce service aujourd'hui.") from exc
        raise

    try:
        # 2. Numéro suivant : compteur atomique, jamais deux fois le même numéro.
        seq = CONTROL.update_item(Key={"pk": f"SEQ#{queue_key}"}, UpdateExpression="ADD last_number :one",
                                  ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
        number = f"{letter}-{int(seq['Attributes']['last_number']):03d}"
        ticket = {
            "ticket_id": f"{agency_id}_{day_compact}_{number}", "agency_id": agency_id,
            "agency_name": agency.get("name"), "service": service.get("code"), "number": number,
            "status": "WAITING", "priority": "NORMAL", "queue_key": queue_key, "waiting_since": f"{now}#{number}",
            "created_at": now, "updated_at": now, "owner_sub": user["sub"], "cin_hash": cin_hash,
        }
        # 3. Ticket en attente.
        TICKETS.put_item(Item=ticket, ConditionExpression="attribute_not_exists(ticket_id)")
    except Exception:
        CONTROL.delete_item(Key=_lock_key(cin_hash, queue_key))  # compensation : le citoyen peut réessayer
        raise

    # 4. Le verrou pointe vers le ticket (utile pour le support).
    CONTROL.update_item(Key=_lock_key(cin_hash, queue_key), UpdateExpression="SET ticket_id = :t",
                        ExpressionAttributeValues={":t": ticket["ticket_id"]})
    dc.log("ticket_created", ticket_id=ticket["ticket_id"], agency_id=agency_id, number=number)
    return dc.response(201, _with_position(ticket))


def get_ticket(event):
    ticket_id = dv.path_params(event, {"ticket_id": dv.ticket_id})["ticket_id"]
    user = dc.require_role(event, "CITIZEN", *STAFF)
    ticket = _load(ticket_id)
    roles = set(dc.groups(event))
    if roles & set(STAFF) and user.get("custom:agency_id") == ticket["agency_id"]:
        return dc.response(200, _with_position(ticket))
    if "CITIZEN" in roles and ticket.get("owner_sub") == user["sub"]:
        return dc.response(200, _with_position(ticket))
    if roles & set(STAFF):
        raise dc.ApiError(403, "WRONG_AGENCY", "Ce ticket appartient à une autre agence.")
    raise dc.ApiError(403, "NOT_OWNER", "Ce ticket ne vous appartient pas.")


def cancel_ticket(event):
    ticket_id = dv.path_params(event, {"ticket_id": dv.ticket_id})["ticket_id"]
    user = dc.require_role(event, "CITIZEN")
    ticket = _load(ticket_id)
    if ticket.get("owner_sub") != user["sub"]:
        raise dc.ApiError(403, "NOT_OWNER", "Ce ticket ne vous appartient pas.")
    now = dc.now_iso()
    try:
        updated = TICKETS.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="SET #s = :cancelled, cancelled_at = :now, updated_at = :now REMOVE waiting_since",
            ConditionExpression="#s IN (:waiting, :called)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":cancelled": "CANCELLED", ":waiting": "WAITING", ":called": "CALLED",
                                       ":now": now},
            ReturnValues="ALL_NEW")["Attributes"]
    except ClientError as exc:
        if _conditional_failed(exc):
            raise dc.ApiError(409, "INVALID_TRANSITION",
                              f"Un ticket au statut {ticket['status']} ne peut pas être annulé.") from exc
        raise
    _release_lock(ticket)
    dc.log("ticket_cancelled", ticket_id=ticket_id)
    return dc.response(200, _with_position(updated))


def close_ticket(event):
    ticket_id = dv.path_params(event, {"ticket_id": dv.ticket_id})["ticket_id"]
    status = dv.validate(dv.parse_body(event), {"status": dv.final_status}, required=("status",))["status"]
    user = dc.require_role(event, "AGENT")
    ticket = _load(ticket_id)
    dc.require_agency(user, ticket["agency_id"])
    field = "served_at" if status == "SERVED" else "no_show_at"
    try:
        updated = TICKETS.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression=f"SET #s = :new, {field} = :now, updated_at = :now",
            ConditionExpression="#s = :called",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new": status, ":called": "CALLED", ":now": dc.now_iso()},
            ReturnValues="ALL_NEW")["Attributes"]
    except ClientError as exc:
        if _conditional_failed(exc):
            raise dc.ApiError(409, "INVALID_TRANSITION",
                              f"Passage de {ticket['status']} à {status} impossible (le ticket doit être CALLED).") from exc
        raise
    _release_lock(ticket)
    dc.log("ticket_closed", ticket_id=ticket_id, status=status, by=user.get("sub"))
    return dc.response(200, _with_position(updated))


ROUTES = {
    ("/tickets", "POST"): create_ticket,
    ("/tickets/{ticket_id}", "GET"): get_ticket,
    ("/tickets/{ticket_id}", "DELETE"): cancel_ticket,
    ("/tickets/{ticket_id}", "PATCH"): close_ticket,
}


def lambda_handler(event, context):
    return dc.dispatch(event, context, ROUTES)
