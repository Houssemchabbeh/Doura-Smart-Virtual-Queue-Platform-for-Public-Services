"""Test du script de démo : appels de tickets et passage en SERVED / CANCELLED.

Responsable : Mouhamed.
"""
import argparse
import importlib.util

import boto3
import pytest
from moto import mock_aws

import dynamodb
import seed_data
from conftest import ROOT

spec = importlib.util.spec_from_file_location("demo_notification", ROOT / "scripts" / "demo_notification.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture
def seeded():
    with mock_aws():
        session = boto3.Session(region_name="us-east-1")
        ctx = {"session": session, "stage": "test", "region": "us-east-1", "account_id": "123456789012", "outputs": {}}
        ctx["outputs"].update(dynamodb.deploy(ctx))
        ctx["outputs"].update(seed_data.deploy(ctx))
        table = session.resource("dynamodb").Table(ctx["outputs"]["tickets_table"])
        yield table, ctx["outputs"]["seed_date"]


def _statuses(table, day):
    return {t["number"]: t["status"] for t in demo.today_tickets(table, "AG-TUN-03", day)}


def test_call_then_serve_and_cancel(seeded):
    table, day = seeded
    demo.cmd_call(table, "AG-TUN-03", day, argparse.Namespace(count=3, letter="A"))
    statuses = _statuses(table, day)
    assert [n for n, s in statuses.items() if s == "CALLED"] == ["A-001", "A-002", "A-003"]

    demo.cmd_serve(table, "AG-TUN-03", day, argparse.Namespace(number="A-002"))
    demo.cmd_cancel(table, "AG-TUN-03", day, argparse.Namespace(number="A-007"))
    statuses = _statuses(table, day)
    assert statuses["A-002"] == "SERVED" and statuses["A-007"] == "CANCELLED"

    # Un ticket servi ne peut plus être servi à nouveau.
    with pytest.raises(SystemExit):
        demo.cmd_serve(table, "AG-TUN-03", day, argparse.Namespace(number="A-002"))


def test_called_ticket_leaves_waiting_queue(seeded):
    table, day = seeded
    demo.cmd_call(table, "AG-TUN-03", day, argparse.Namespace(count=8, letter="A"))
    demo.cmd_call(table, "AG-TUN-03", day, argparse.Namespace(count=1, letter="A"))  # file vide : rien à appeler
    assert set(_statuses(table, day).values()) == {"CALLED"}
