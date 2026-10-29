"""Tests de la Lambda agencies : lecture publique et CRUD des services par le responsable d'agence.

Responsable : Mouhamed.
"""
import pytest

from api_events import MANAGER_ARI, MANAGER_TUN, AGENT_TUN, CITIZEN, call, error_code, event, seeded_aws

SERVICES = "/agencies/{agency_id}/services"
SERVICE = "/agencies/{agency_id}/services/{letter}"
NEW_SERVICE = {"letter": "E", "code": "PERMIS_BATIR", "name": "Permis de bâtir", "hours": "Lun-Ven 08:00-12:00",
               "required_documents": ["Carte d'identité", "Plan du terrain"], "avg_service_min": 10}


@pytest.fixture
def agencies():
    with seeded_aws("agencies") as (outputs, handlers):
        yield handlers["agencies"]


def test_list_agencies(agencies):
    status, body = call(agencies, event("/agencies", "GET"))
    assert status == 200
    assert [a["agency_id"] for a in body["agencies"]] == ["AG-ARI-01", "AG-SFX-02", "AG-TUN-03"]
    assert len(body["agencies"][0]["services"]) == 4


def test_get_agency(agencies):
    status, body = call(agencies, event("/agencies/{agency_id}", "GET", {"agency_id": "AG-TUN-03"}))
    assert status == 200 and body["city"] == "Tunis" and len(body["counters"]) == 4

    status, body = call(agencies, event("/agencies/{agency_id}", "GET", {"agency_id": "AG-XXX-99"}))
    assert status == 404 and error_code(body) == "AGENCY_NOT_FOUND"

    status, body = call(agencies, event("/agencies/{agency_id}", "GET", {"agency_id": "tunis"}))
    assert status == 400 and error_code(body) == "VALIDATION_ERROR"


def test_list_services(agencies):
    status, body = call(agencies, event(SERVICES, "GET", {"agency_id": "AG-TUN-03"}))
    assert status == 200 and [s["letter"] for s in body["services"]] == ["A", "B", "C", "D"]
    status, body = call(agencies, event(SERVICES, "GET", {"agency_id": "AG-XXX-99"}))
    assert status == 404


def test_service_crud_by_manager(agencies):
    path = {"agency_id": "AG-TUN-03"}
    status, body = call(agencies, event(SERVICES, "POST", path, NEW_SERVICE, MANAGER_TUN))
    assert status == 201 and body["code"] == "PERMIS_BATIR"

    status, body = call(agencies, event(SERVICES, "POST", path, NEW_SERVICE, MANAGER_TUN))
    assert status == 409 and error_code(body) == "SERVICE_EXISTS"

    one = {"agency_id": "AG-TUN-03", "letter": "E"}
    status, body = call(agencies, event(SERVICE, "PUT", one, {"avg_service_min": 12, "name": "  Permis  "}, MANAGER_TUN))
    assert status == 200 and body["avg_service_min"] == 12 and body["name"] == "Permis"
    assert body["hours"] == NEW_SERVICE["hours"]  # champs non fournis inchangés

    status, body = call(agencies, event(SERVICE, "DELETE", one, user=MANAGER_TUN))
    assert status == 204 and body is None
    status, body = call(agencies, event(SERVICE, "DELETE", one, user=MANAGER_TUN))
    assert status == 404 and error_code(body) == "SERVICE_NOT_FOUND"


@pytest.mark.parametrize("body, fields", [
    ({**NEW_SERVICE, "letter": "e"}, ["letter"]),
    ({**NEW_SERVICE, "code": "permis"}, ["code"]),
    ({**NEW_SERVICE, "name": "ab"}, ["name"]),
    ({**NEW_SERVICE, "hours": "8h"}, ["hours"]),
    ({**NEW_SERVICE, "required_documents": "CIN"}, ["required_documents"]),
    ({**NEW_SERVICE, "avg_service_min": 0}, ["avg_service_min"]),
    ({**NEW_SERVICE, "extra": True}, ["extra"]),
    ({"letter": "E"}, ["avg_service_min", "code", "hours", "name"]),
])
def test_create_service_validation(agencies, body, fields):
    status, resp = call(agencies, event(SERVICES, "POST", {"agency_id": "AG-TUN-03"}, body, MANAGER_TUN))
    assert status == 400 and error_code(resp) == "VALIDATION_ERROR"
    for field in fields:
        assert field in resp["error"]["message"]


def test_invalid_json(agencies):
    status, body = call(agencies, event(SERVICES, "POST", {"agency_id": "AG-TUN-03"}, "{pas du json", MANAGER_TUN))
    assert status == 400 and error_code(body) == "INVALID_JSON"


def test_update_needs_at_least_one_field(agencies):
    status, body = call(agencies, event(SERVICE, "PUT", {"agency_id": "AG-TUN-03", "letter": "A"}, {}, MANAGER_TUN))
    assert status == 400 and error_code(body) == "VALIDATION_ERROR"
    status, body = call(agencies, event(SERVICE, "PUT", {"agency_id": "AG-TUN-03", "letter": "Z"},
                                        {"name": "Inconnu"}, MANAGER_TUN))
    assert status == 404 and error_code(body) == "SERVICE_NOT_FOUND"


@pytest.mark.parametrize("user, status, code", [
    (None, 401, "UNAUTHORIZED"),
    (CITIZEN, 403, "FORBIDDEN"),
    (AGENT_TUN, 403, "FORBIDDEN"),
    (MANAGER_ARI, 403, "WRONG_AGENCY"),
])
def test_only_manager_of_agency_can_write(agencies, user, status, code):
    resp_status, body = call(agencies, event(SERVICES, "POST", {"agency_id": "AG-TUN-03"}, NEW_SERVICE, user))
    assert resp_status == status and error_code(body) == code


def test_cannot_delete_service_with_waiting_tickets(agencies):
    # Le seed place 8 tickets en attente sur le service A de AG-TUN-03.
    status, body = call(agencies, event(SERVICE, "DELETE", {"agency_id": "AG-TUN-03", "letter": "A"}, user=MANAGER_TUN))
    assert status == 409 and error_code(body) == "SERVICE_HAS_WAITING_TICKETS"


def test_unknown_route(agencies):
    status, body = call(agencies, event("/agencies", "DELETE"))
    assert status == 405 and error_code(body) == "METHOD_NOT_ALLOWED"
