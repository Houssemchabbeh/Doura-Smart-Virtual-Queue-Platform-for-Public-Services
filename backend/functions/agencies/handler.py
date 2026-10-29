"""Lambda agencies : consultation des agences (public) et CRUD des services (responsable d'agence).

Responsable : Mouhamed.
Routes :
    GET    /agencies                                   public     liste des agences avec services et guichets
    GET    /agencies/{agency_id}                       public     une agence
    GET    /agencies/{agency_id}/services              public     services d'une agence
    POST   /agencies/{agency_id}/services              MANAGER    créer un service        -> 201
    PUT    /agencies/{agency_id}/services/{letter}     MANAGER    modifier un service     -> 200
    DELETE /agencies/{agency_id}/services/{letter}     MANAGER    supprimer un service    -> 204
Table agencies : PK agency_id, SK item = META | SERVICE#<lettre> | COUNTER#<id>.
"""
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import doura_common as dc
import doura_validation as dv

DYNAMODB = boto3.resource("dynamodb")
TABLE = DYNAMODB.Table(os.environ["AGENCIES_TABLE"])
TICKETS = DYNAMODB.Table(os.environ["TICKETS_TABLE"])

SERVICE_FIELDS = ("code", "name", "letter", "hours", "required_documents", "avg_service_min")
SERVICE_SCHEMA = {"letter": dv.letter, "code": dv.code, "name": dv.name, "hours": dv.hours,
                  "required_documents": dv.required_documents, "avg_service_min": dv.avg_service_min}
UPDATE_SCHEMA = {k: v for k, v in SERVICE_SCHEMA.items() if k != "letter"}  # la lettre est la clé : non modifiable


# --- Lecture -------------------------------------------------------------------------------------------------

def _service(item: dict) -> dict:
    return {k: item[k] for k in SERVICE_FIELDS if k in item}


def _counter(item: dict) -> dict:
    return {k: item[k] for k in ("counter_id", "services", "open") if k in item}


def _group(items: list) -> dict:
    """Regroupe les items d'une ou plusieurs agences : {agency_id: {..., services, counters}}."""
    agencies = {}
    for item in items:
        agency = agencies.setdefault(item["agency_id"], {"agency_id": item["agency_id"], "services": [], "counters": []})
        if item["item"] == "META":
            agency["_found"] = True
            agency.update({k: item[k] for k in ("name", "city", "address") if k in item})
        elif item["item"].startswith("SERVICE#"):
            agency["services"].append(_service(item))
        elif item["item"].startswith("COUNTER#"):
            agency["counters"].append(_counter(item))
    for agency in agencies.values():
        agency["services"].sort(key=lambda s: s.get("letter", ""))
        agency["counters"].sort(key=lambda c: c.get("counter_id", ""))
    return agencies


def _query_agency(agency_id: str, prefix: str = None) -> list:
    condition = Key("agency_id").eq(agency_id)
    if prefix:
        condition = condition & Key("item").begins_with(prefix)
    items, kwargs = [], {"KeyConditionExpression": condition}
    while True:
        page = TABLE.query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _require_agency_exists(agency_id: str) -> dict:
    meta = TABLE.get_item(Key={"agency_id": agency_id, "item": "META"}).get("Item")
    if not meta:
        raise dc.ApiError(404, "AGENCY_NOT_FOUND", f"Agence {agency_id} introuvable.")
    return meta


def list_agencies(event):
    items, kwargs = [], {}
    while True:  # table petite (quelques agences) : un Scan paginé suffit
        page = TABLE.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    result = sorted((a for a in _group(items).values() if a.pop("_found", False)), key=lambda a: a["agency_id"])
    dc.log("agencies_listed", count=len(result))
    return dc.response(200, {"agencies": result})


def get_agency(event):
    agency_id = dv.path_params(event, {"agency_id": dv.agency_id})["agency_id"]
    agency = _group(_query_agency(agency_id)).get(agency_id)
    if not agency or not agency.pop("_found", False):
        raise dc.ApiError(404, "AGENCY_NOT_FOUND", f"Agence {agency_id} introuvable.")
    return dc.response(200, agency)


def list_services(event):
    agency_id = dv.path_params(event, {"agency_id": dv.agency_id})["agency_id"]
    _require_agency_exists(agency_id)
    services = sorted((_service(i) for i in _query_agency(agency_id, "SERVICE#")), key=lambda s: s["letter"])
    return dc.response(200, {"agency_id": agency_id, "services": services})


# --- Écriture (responsable de l'agence) ----------------------------------------------------------------------

def create_service(event):
    agency_id = dv.path_params(event, {"agency_id": dv.agency_id})["agency_id"]
    data = dv.validate(dv.parse_body(event), SERVICE_SCHEMA,
                       required=("letter", "code", "name", "hours", "avg_service_min"))
    data.setdefault("required_documents", [])
    user = dc.require_role(event, "MANAGER")
    dc.require_agency(user, agency_id)
    _require_agency_exists(agency_id)

    item = {"agency_id": agency_id, "item": f"SERVICE#{data['letter']}", **data, "updated_at": dc.now_iso()}
    try:
        TABLE.put_item(Item=item, ConditionExpression="attribute_not_exists(agency_id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise dc.ApiError(409, "SERVICE_EXISTS", f"Le service {data['letter']} existe déjà.") from exc
        raise
    dc.log("service_created", agency_id=agency_id, letter=data["letter"], by=user.get("sub"))
    return dc.response(201, _service(item))


def update_service(event):
    params = dv.path_params(event, {"agency_id": dv.agency_id, "letter": dv.letter})
    data = dv.validate(dv.parse_body(event), UPDATE_SCHEMA, at_least_one=True)
    user = dc.require_role(event, "MANAGER")
    dc.require_agency(user, params["agency_id"])

    data["updated_at"] = dc.now_iso()
    names = {f"#{k}": k for k in data}  # "name" est un mot réservé DynamoDB
    values = {f":{k}": v for k, v in data.items()}
    try:
        updated = TABLE.update_item(
            Key={"agency_id": params["agency_id"], "item": f"SERVICE#{params['letter']}"},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in data),
            ConditionExpression="attribute_exists(agency_id)",
            ExpressionAttributeNames=names, ExpressionAttributeValues=values, ReturnValues="ALL_NEW")["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise dc.ApiError(404, "SERVICE_NOT_FOUND", f"Service {params['letter']} introuvable.") from exc
        raise
    dc.log("service_updated", agency_id=params["agency_id"], letter=params["letter"], fields=sorted(data))
    return dc.response(200, _service(updated))


def delete_service(event):
    params = dv.path_params(event, {"agency_id": dv.agency_id, "letter": dv.letter})
    user = dc.require_role(event, "MANAGER")
    dc.require_agency(user, params["agency_id"])

    # Refus si des citoyens attendent encore pour ce service aujourd'hui.
    day, _ = dc.today()
    waiting = TICKETS.query(IndexName="WaitingQueue", Limit=1,
                            KeyConditionExpression=Key("queue_key").eq(f"{params['agency_id']}#{day}#{params['letter']}"))
    if waiting.get("Items"):
        raise dc.ApiError(409, "SERVICE_HAS_WAITING_TICKETS",
                          "Des tickets sont en attente pour ce service aujourd'hui : suppression impossible.")
    try:
        TABLE.delete_item(Key={"agency_id": params["agency_id"], "item": f"SERVICE#{params['letter']}"},
                          ConditionExpression="attribute_exists(agency_id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise dc.ApiError(404, "SERVICE_NOT_FOUND", f"Service {params['letter']} introuvable.") from exc
        raise
    dc.log("service_deleted", agency_id=params["agency_id"], letter=params["letter"])
    return dc.response(204)


ROUTES = {
    ("/agencies", "GET"): list_agencies,
    ("/agencies/{agency_id}", "GET"): get_agency,
    ("/agencies/{agency_id}/services", "GET"): list_services,
    ("/agencies/{agency_id}/services", "POST"): create_service,
    ("/agencies/{agency_id}/services/{letter}", "PUT"): update_service,
    ("/agencies/{agency_id}/services/{letter}", "DELETE"): delete_service,
}


def lambda_handler(event, context):
    return dc.dispatch(event, context, ROUTES)
