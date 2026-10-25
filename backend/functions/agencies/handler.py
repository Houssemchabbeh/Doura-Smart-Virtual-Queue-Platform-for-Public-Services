"""Lambda agencies : GET /agencies (public).

Responsable : Mouhamed.
Renvoie les agences avec leurs services, horaires et pièces requises.
Table agencies : PK agency_id, SK item = META | SERVICE#<lettre> | COUNTER#<id>.
"""
import os

import boto3

import doura_common as dc

TABLE = boto3.resource("dynamodb").Table(os.environ["AGENCIES_TABLE"])


def lambda_handler(event, context):
    items = []
    scan_kwargs = {}
    while True:  # la table est petite ; un Scan paginé suffit pour le Sprint 1
        page = TABLE.scan(**scan_kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        scan_kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    agencies = {}
    for item in items:
        agency = agencies.setdefault(item["agency_id"], {"agency_id": item["agency_id"], "services": [], "counters": []})
        if item["item"] == "META":
            agency.update({k: item[k] for k in ("name", "city", "address") if k in item})
        elif item["item"].startswith("SERVICE#"):
            fields = ("code", "name", "letter", "hours", "required_documents", "avg_service_min")
            agency["services"].append({k: item[k] for k in fields if k in item})
        elif item["item"].startswith("COUNTER#"):
            agency["counters"].append({k: item[k] for k in ("counter_id", "services", "open") if k in item})

    result = sorted(agencies.values(), key=lambda a: a["agency_id"])
    for agency in result:
        agency["services"].sort(key=lambda s: s.get("letter", ""))
        agency["counters"].sort(key=lambda c: c.get("counter_id", ""))
    dc.log("agencies_listed", count=len(result))
    return dc.response(200, {"agencies": result})
