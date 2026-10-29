"""Tests de la Lambda queue : appel du prochain ticket (FIFO, concurrence, droits, guichet fermé).

Responsable : Houssem.
"""
import boto3
import pytest
from botocore.exceptions import ClientError

from api_events import AGENT_ARI, AGENT_TUN, MANAGER_TUN, call, error_code, event, seeded_aws

ROUTE = "/agencies/{agency_id}/counters/{counter_id}/call-next"


@pytest.fixture
def env():
    with seeded_aws("queue") as (outputs, handlers):
        yield outputs, handlers["queue"]


def _call_next(queue, agency="AG-TUN-03", counter="C01", user=AGENT_TUN):
    return call(queue, event(ROUTE, "POST", {"agency_id": agency, "counter_id": counter}, user=user))


def test_calls_in_arrival_order(env):
    _, queue = env
    numbers = []
    for _ in range(3):
        status, body = _call_next(queue)
        assert status == 200 and body["status"] == "CALLED" and body["counter_id"] == "C01"
        numbers.append(body["number"])
    assert numbers == ["A-001", "A-002", "A-003"]


def test_fifo_across_services(env):
    outputs, queue = env
    tickets = boto3.resource("dynamodb", region_name="us-east-1").Table(outputs["tickets_table"])
    day = outputs["seed_date"]
    # Un ticket B arrivé avant tous les tickets A du seed doit passer en premier au guichet C01 (services A et B).
    tickets.put_item(Item={"ticket_id": f"AG-TUN-03_{day.replace('-', '')}_B-001", "agency_id": "AG-TUN-03",
                           "service": "ETAT_CIVIL", "number": "B-001", "status": "WAITING",
                           "queue_key": f"AG-TUN-03#{day}#B", "waiting_since": "2000-01-01T00:00:00Z#B-001"})
    assert _call_next(queue)[1]["number"] == "B-001"
    assert _call_next(queue)[1]["number"] == "A-001"


def test_empty_queue_returns_204(env):
    _, queue = env
    status, body = _call_next(queue, counter="C03")  # service C : aucun ticket
    assert status == 204 and body is None


def test_conflict_moves_to_next_candidate(env, monkeypatch):
    _, queue = env
    real_update = queue.TICKETS.update_item
    calls = []

    def first_one_taken(**kwargs):
        calls.append(kwargs["Key"]["ticket_id"])
        if len(calls) == 1:  # un autre guichet a appelé A-001 juste avant nous
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "pris"}}, "UpdateItem")
        return real_update(**kwargs)

    monkeypatch.setattr(queue.TICKETS, "update_item", first_one_taken)
    status, body = _call_next(queue)
    assert status == 200 and body["number"] == "A-002"


def test_conflict_retry_after_three_attempts(env, monkeypatch):
    _, queue = env

    def always_taken(**kwargs):
        raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "pris"}}, "UpdateItem")

    monkeypatch.setattr(queue.TICKETS, "update_item", always_taken)
    status, body = _call_next(queue)
    assert status == 409 and error_code(body) == "CALL_CONFLICT_RETRY"


def test_closed_counter(env):
    outputs, queue = env
    agencies = boto3.resource("dynamodb", region_name="us-east-1").Table(outputs["agencies_table"])
    agencies.update_item(Key={"agency_id": "AG-TUN-03", "item": "COUNTER#C01"},
                         UpdateExpression="SET #o = :f", ExpressionAttributeNames={"#o": "open"},
                         ExpressionAttributeValues={":f": False})
    status, body = _call_next(queue)
    assert status == 409 and error_code(body) == "INVALID_TRANSITION"


@pytest.mark.parametrize("kwargs, status, code", [
    ({"user": AGENT_ARI}, 403, "WRONG_AGENCY"),
    ({"user": MANAGER_TUN}, 403, "FORBIDDEN"),
    ({"user": None}, 401, "UNAUTHORIZED"),
    ({"counter": "C09"}, 404, "COUNTER_NOT_FOUND"),
    ({"counter": "guichet-1"}, 400, "VALIDATION_ERROR"),
])
def test_errors(env, kwargs, status, code):
    _, queue = env
    resp_status, body = _call_next(queue, **kwargs)
    assert resp_status == status and error_code(body) == code
