"""Tests de la Lambda tickets : création, suivi, annulation, clôture, validation et droits.

Responsable : Houssem.
"""
import boto3
import pytest
from botocore.exceptions import ClientError

from api_events import AGENT_ARI, AGENT_TUN, CIN, CITIZEN, CITIZEN_2, MANAGER_TUN, call, error_code, event, seeded_aws

ONE = "/tickets/{ticket_id}"
BODY = {"agency_id": "AG-TUN-03", "service": "A", "cin": CIN}


@pytest.fixture
def env():
    with seeded_aws("tickets", "queue") as (outputs, handlers):
        yield outputs, handlers["tickets"], handlers["queue"]


def _create(tickets, body=BODY, user=CITIZEN):
    return call(tickets, event("/tickets", "POST", body=body, user=user))


def _table(outputs, key):
    return boto3.resource("dynamodb", region_name="us-east-1").Table(outputs[key])


def _call_next(queue, agency="AG-TUN-03", counter="C01", user=AGENT_TUN):
    path = {"agency_id": agency, "counter_id": counter}
    return call(queue, event("/agencies/{agency_id}/counters/{counter_id}/call-next", "POST", path, user=user))


def test_create_ticket(env):
    outputs, tickets, _ = env
    status, body = _create(tickets)
    assert status == 201
    assert body["number"] == "A-009"  # le seed a déjà émis A-001..A-008
    assert body["status"] == "WAITING" and body["service"] == "LEGALISATION"
    assert body["position"] == 9 and body["estimated_wait_min"] == 18  # 9 x 4 min / 2 guichets ouverts (C01, C02)
    assert body["ticket_id"].startswith("AG-TUN-03_") and body["ticket_id"].endswith("_A-009")
    assert not {"cin_hash", "owner_sub", "queue_key"} & set(body)

    stored = _table(outputs, "tickets_table").get_item(Key={"ticket_id": body["ticket_id"]})["Item"]
    assert stored["owner_sub"] == CITIZEN["sub"] and len(stored["cin_hash"]) == 64 and CIN not in str(stored)


def test_duplicate_active_ticket_is_refused_without_consuming_a_number(env):
    outputs, tickets, _ = env
    assert _create(tickets)[0] == 201
    status, body = _create(tickets)
    assert status == 409 and error_code(body) == "TICKET_ALREADY_ACTIVE"
    # Un autre citoyen obtient le numéro suivant : le refus n'a consommé aucun numéro.
    status, body = _create(tickets, {**BODY, "cin": "87654321"}, CITIZEN_2)
    assert status == 201 and body["number"] == "A-010"


def test_same_citizen_can_take_another_service(env):
    _, tickets, _ = env
    assert _create(tickets)[0] == 201
    status, body = _create(tickets, {**BODY, "service": "B"})
    assert status == 201 and body["number"] == "B-001"


@pytest.mark.parametrize("body, field", [
    ({**BODY, "cin": "1234"}, "cin"),
    ({**BODY, "cin": 12345678}, "cin"),
    ({**BODY, "agency_id": "TUNIS"}, "agency_id"),
    ({**BODY, "service": "AB"}, "service"),
    ({"agency_id": "AG-TUN-03", "service": "A"}, "cin"),
    ({**BODY, "priority": "VIP"}, "priority"),
])
def test_create_validation(env, body, field):
    _, tickets, _ = env
    status, resp = _create(tickets, body)
    assert status == 400 and error_code(resp) == "VALIDATION_ERROR" and field in resp["error"]["message"]


def test_create_invalid_json_and_unknown_service(env):
    _, tickets, _ = env
    status, body = _create(tickets, "[1, 2]")
    assert status == 400 and error_code(body) == "INVALID_JSON"
    status, body = _create(tickets, {**BODY, "service": "Z"})
    assert status == 404 and error_code(body) == "SERVICE_NOT_FOUND"
    status, body = _create(tickets, {**BODY, "agency_id": "AG-XXX-99"})
    assert status == 404 and error_code(body) == "AGENCY_NOT_FOUND"


@pytest.mark.parametrize("user, status, code", [(None, 401, "UNAUTHORIZED"), (AGENT_TUN, 403, "FORBIDDEN")])
def test_only_citizens_create_tickets(env, user, status, code):
    _, tickets, _ = env
    resp_status, body = _create(tickets, user=user)
    assert resp_status == status and error_code(body) == code


def test_compensation_when_ticket_write_fails(env, monkeypatch):
    outputs, tickets, _ = env

    def broken_put(**kwargs):
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "panne simulée"}}, "PutItem")

    monkeypatch.setattr(tickets.TICKETS, "put_item", broken_put)
    status, body = _create(tickets)
    assert status == 500 and error_code(body) == "INTERNAL_ERROR"
    assert "panne" not in body["error"]["message"]  # détail seulement dans les logs
    locks = [i for i in _table(outputs, "control_table").scan()["Items"] if i["pk"].startswith("ACTIVE#")]
    assert locks == []  # le verrou a été supprimé : le citoyen peut réessayer

    monkeypatch.undo()
    assert _create(tickets)[0] == 201


def test_fifty_sequential_tickets_get_consecutive_numbers(env):
    _, tickets, _ = env
    numbers = []
    for i in range(50):
        user = {"sub": f"sub-{i}", "cognito:groups": "CITIZEN"}
        status, body = _create(tickets, {"agency_id": "AG-TUN-03", "service": "B", "cin": f"{i:08d}"}, user)
        assert status == 201
        numbers.append(body["number"])
    assert numbers == [f"B-{n:03d}" for n in range(1, 51)]


def test_get_ticket_access_rules(env):
    _, tickets, _ = env
    ticket_id = _create(tickets)[1]["ticket_id"]
    path = {"ticket_id": ticket_id}
    for user in (CITIZEN, AGENT_TUN, MANAGER_TUN):
        status, body = call(tickets, event(ONE, "GET", path, user=user))
        assert status == 200 and body["position"] == 9
    status, body = call(tickets, event(ONE, "GET", path, user=CITIZEN_2))
    assert status == 403 and error_code(body) == "NOT_OWNER"
    status, body = call(tickets, event(ONE, "GET", path, user=AGENT_ARI))
    assert status == 403 and error_code(body) == "WRONG_AGENCY"
    status, body = call(tickets, event(ONE, "GET", {"ticket_id": "AG-TUN-03_20000101_A-001"}, user=CITIZEN))
    assert status == 404 and error_code(body) == "TICKET_NOT_FOUND"
    status, body = call(tickets, event(ONE, "GET", {"ticket_id": "AG-TUN-03#2026-10-26#A-001"}, user=CITIZEN))
    assert status == 400 and error_code(body) == "VALIDATION_ERROR"


def test_cancel_ticket(env):
    outputs, tickets, _ = env
    ticket_id = _create(tickets)[1]["ticket_id"]
    path = {"ticket_id": ticket_id}
    status, body = call(tickets, event(ONE, "DELETE", path, user=CITIZEN_2))
    assert status == 403 and error_code(body) == "NOT_OWNER"

    status, body = call(tickets, event(ONE, "DELETE", path, user=CITIZEN))
    assert status == 200 and body["status"] == "CANCELLED" and body["position"] == 0 and body["cancelled_at"]
    status, body = call(tickets, event(ONE, "DELETE", path, user=CITIZEN))
    assert status == 409 and error_code(body) == "INVALID_TRANSITION"
    # Le verrou est libéré : le citoyen peut reprendre un ticket.
    assert _create(tickets)[0] == 201


def test_agent_closes_called_ticket(env):
    _, tickets, queue = env
    ticket_id = "{}_{}_A-001".format("AG-TUN-03", _create(tickets)[1]["ticket_id"].split("_")[1])
    path = {"ticket_id": ticket_id}

    status, body = call(tickets, event(ONE, "PATCH", path, {"status": "SERVED"}, AGENT_TUN))
    assert status == 409 and error_code(body) == "INVALID_TRANSITION"  # encore WAITING

    assert _call_next(queue)[1]["ticket_id"] == ticket_id
    status, body = call(tickets, event(ONE, "PATCH", path, {"status": "SERVED"}, AGENT_ARI))
    assert status == 403 and error_code(body) == "WRONG_AGENCY"
    status, body = call(tickets, event(ONE, "PATCH", path, {"status": "SERVED"}, CITIZEN))
    assert status == 403 and error_code(body) == "FORBIDDEN"
    status, body = call(tickets, event(ONE, "PATCH", path, {"status": "CANCELLED"}, AGENT_TUN))
    assert status == 400 and error_code(body) == "VALIDATION_ERROR"

    status, body = call(tickets, event(ONE, "PATCH", path, {"status": "SERVED"}, AGENT_TUN))
    assert status == 200 and body["status"] == "SERVED" and body["served_at"]
    status, body = call(tickets, event(ONE, "PATCH", path, {"status": "NO_SHOW"}, AGENT_TUN))
    assert status == 409 and error_code(body) == "INVALID_TRANSITION"


def test_no_show_releases_lock(env):
    _, tickets, queue = env
    # Le citoyen prend le seul ticket du service B, l'agent l'appelle au guichet C02 puis le marque absent.
    ticket_id = _create(tickets, {**BODY, "service": "B"})[1]["ticket_id"]
    for _ in range(9):  # vide d'abord la file A (8 tickets du seed) et appelle B-001
        _call_next(queue, counter="C02")
    status, body = call(tickets, event(ONE, "PATCH", {"ticket_id": ticket_id}, {"status": "NO_SHOW"}, AGENT_TUN))
    assert status == 200 and body["status"] == "NO_SHOW" and body["no_show_at"]
    assert _create(tickets, {**BODY, "service": "B"})[0] == 201
