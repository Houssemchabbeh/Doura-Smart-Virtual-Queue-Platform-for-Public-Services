"""Script de démo de la Sprint Review : fait vivre l'écran d'affichage et déclenche les e-mails.

Responsable : Mouhamed.
Utilise les tickets FICTIFS créés par infra/seed_data.py et les sorties infra/outputs-<stage>.json.

Exemples (depuis la racine du dépôt) :
    python scripts/demo_notification.py --stage demo status
    python scripts/demo_notification.py --stage demo call --count 3     # 3 tickets appelés -> écran public
    python scripts/demo_notification.py --stage demo serve               # dernier appelé -> SERVED -> e-mail
    python scripts/demo_notification.py --stage demo cancel --number A-007   # -> CANCELLED -> e-mail
Remise à zéro de la démo : python infra/deploy.py --stage demo --only seed
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
COUNTERS = ["C01", "C02", "C03", "C04"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_outputs(stage: str) -> dict:
    path = ROOT / "infra" / f"outputs-{stage}.json"
    if not path.exists():
        sys.exit(f"{path} introuvable : lancez d'abord python infra/deploy.py --stage {stage}")
    return json.loads(path.read_text())


def today_tickets(table, agency: str, day: str) -> list:
    items, kwargs = [], {"FilterExpression": Attr("agency_id").eq(agency) & Attr("ticket_id").begins_with(f"{agency}#{day}#")}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return sorted(items, key=lambda t: t["number"])
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def cmd_status(table, agency, day, args):
    tickets = today_tickets(table, agency, day)
    if not tickets:
        print("Aucun ticket aujourd'hui : relancez le seed (deploy.py --only seed).")
    for t in tickets:
        print(f"{t['number']:<7} {t['status']:<10} guichet={t.get('counter_id') or '-':<4} appelé={t.get('called_at', '-')}")


def cmd_call(table, agency, day, args):
    queue_key = f"{agency}#{day}#{args.letter}"
    waiting = table.query(IndexName="WaitingQueue", KeyConditionExpression=Key("queue_key").eq(queue_key),
                          ScanIndexForward=True, Limit=args.count + 5).get("Items", [])
    called = 0
    for ticket in waiting:
        if called == args.count:
            break
        counter = COUNTERS[called % 2]  # services A/B : guichets C01 et C02
        try:
            # Même logique que la future Lambda call-next : écriture conditionnelle, jamais deux fois le même ticket.
            table.update_item(
                Key={"ticket_id": ticket["ticket_id"]},
                UpdateExpression="SET #s = :called, counter_id = :c, called_at = :now, updated_at = :now "
                                 "REMOVE waiting_since",
                ConditionExpression="#s = :waiting",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":called": "CALLED", ":waiting": "WAITING", ":c": counter, ":now": now_iso()})
            print(f"Appelé : {ticket['number']} au guichet {counter}")
            called += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    if called == 0:
        print("Personne en attente.")


def _set_final_status(table, agency, day, args, status, time_field):
    tickets = today_tickets(table, agency, day)
    if args.number:
        target = next((t for t in tickets if t["number"] == args.number), None)
    else:
        called = [t for t in tickets if t["status"] == "CALLED"]
        target = max(called, key=lambda t: t["called_at"]) if called else None
    if not target:
        sys.exit("Aucun ticket correspondant (appelez d'abord des tickets avec la commande call).")
    allowed = ["CALLED"] if status == "SERVED" else ["WAITING", "CALLED"]
    try:
        table.update_item(
            Key={"ticket_id": target["ticket_id"]},
            UpdateExpression=f"SET #s = :new, {time_field} = :now, updated_at = :now REMOVE waiting_since",
            ConditionExpression="#s IN (" + ", ".join(f":a{i}" for i in range(len(allowed))) + ")",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new": status, ":now": now_iso(), **{f":a{i}": a for i, a in enumerate(allowed)}})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            sys.exit(f"{target['number']} est au statut {target['status']} : passage à {status} impossible.")
        raise
    print(f"{target['number']} -> {status}. L'e-mail doit arriver dans la boîte de démo (quelques secondes).")


def cmd_serve(table, agency, day, args):
    _set_final_status(table, agency, day, args, "SERVED", "served_at")


def cmd_cancel(table, agency, day, args):
    _set_final_status(table, agency, day, args, "CANCELLED", "cancelled_at")


def main():
    parser = argparse.ArgumentParser(description="Démo Doura : appels, écran public et notifications e-mail")
    parser.add_argument("--stage", default="demo")
    parser.add_argument("--agency", help="agence (par défaut : celle du seed)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="liste les tickets du jour")
    p_call = sub.add_parser("call", help="appelle les prochains tickets en attente")
    p_call.add_argument("--count", type=int, default=1)
    p_call.add_argument("--letter", default="A", help="lettre du service (A par défaut)")
    p_serve = sub.add_parser("serve", help="passe un ticket appelé en SERVED (e-mail)")
    p_serve.add_argument("--number", help="ex. A-001 ; par défaut le dernier appelé")
    p_cancel = sub.add_parser("cancel", help="passe un ticket en CANCELLED (e-mail)")
    p_cancel.add_argument("--number", help="ex. A-007 ; par défaut le dernier appelé")
    args = parser.parse_args()

    outputs = load_outputs(args.stage)
    agency = args.agency or outputs.get("demo_agency", "AG-TUN-03")
    day = outputs.get("seed_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    table = boto3.Session(region_name="us-east-1").resource("dynamodb").Table(outputs["tickets_table"])
    {"status": cmd_status, "call": cmd_call, "serve": cmd_serve, "cancel": cmd_cancel}[args.command](table, agency, day, args)


if __name__ == "__main__":
    main()
