"""Tests du module de validation (fonctions pures, aucun AWS).

Responsable : Houssem.
"""
import json

import pytest

import doura_validation as dv


@pytest.mark.parametrize("check, good, bad", [
    (dv.agency_id, ["AG-TUN-03", "AG-SFX-99"], ["AG-TUN-3", "ag-tun-03", "AG-TUNI-03", "", None, 3]),
    (dv.letter, ["A", "Z"], ["a", "AB", "1", "", None]),
    (dv.code, ["ETAT_CIVIL", "ABC"], ["AB", "etat", "ETAT-CIVIL", "A" * 31, None]),
    (dv.cin, ["01234567"], ["1234567", "123456789", "1234567a", 12345678, None]),
    (dv.ticket_id, ["AG-TUN-03_20261026_A-047"],
     ["AG-TUN-03#2026-10-26#A-047", "AG-TUN-03_2026102_A-047", "AG-TUN-03_20261026_A-47", None]),
    (dv.counter_id, ["C01"], ["C1", "c01", "X01", None]),
    (dv.final_status, ["SERVED", "NO_SHOW"], ["CALLED", "CANCELLED", "served", None]),
])
def test_formats(check, good, bad):
    for value in good:
        assert check(value) == value
    for value in bad:
        with pytest.raises(ValueError):
            check(value)


def test_text_is_trimmed_and_bounded():
    assert dv.name("  Légalisation  ") == "Légalisation"
    for value in ("ab", "   ab   ", "x" * 81, 42, None):
        with pytest.raises(ValueError):
            dv.name(value)
    assert dv.hours("Lun-Ven 08:00-16:00") == "Lun-Ven 08:00-16:00"
    with pytest.raises(ValueError):
        dv.hours("8-16")


def test_required_documents():
    assert dv.required_documents([]) == []
    assert dv.required_documents([" CIN "]) == ["CIN"]
    for value in (["x"], ["ok"] * 11, "CIN", [1], None):
        with pytest.raises(ValueError):
            dv.required_documents(value)


def test_avg_service_min():
    assert dv.avg_service_min(1) == 1 and dv.avg_service_min(120) == 120
    for value in (0, 121, 4.5, "5", True, None):
        with pytest.raises(ValueError):
            dv.avg_service_min(value)


def test_parse_body():
    assert dv.parse_body({"body": json.dumps({"a": 1})}) == {"a": 1}
    assert dv.parse_body({"body": None}) == {}
    for raw in ("{pas du json", "[1, 2]", "42", '"texte"'):
        with pytest.raises(dv.InvalidJson):
            dv.parse_body({"body": raw})


def test_validate_reports_every_bad_field():
    schema = {"name": dv.name, "avg_service_min": dv.avg_service_min, "hours": dv.hours}
    with pytest.raises(dv.ValidationError) as err:
        dv.validate({"name": "x", "extra": 1}, schema, required=("name", "avg_service_min"))
    assert err.value.fields == ["avg_service_min", "extra", "name"]
    assert "avg_service_min" in err.value.message


def test_validate_returns_cleaned_values():
    assert dv.validate({"name": "  Paiement "}, {"name": dv.name}, required=("name",)) == {"name": "Paiement"}


def test_validate_at_least_one():
    with pytest.raises(dv.ValidationError):
        dv.validate({}, {"name": dv.name}, at_least_one=True)


def test_path_params():
    event = {"pathParameters": {"agency_id": "AG-TUN-03", "letter": "a"}}
    with pytest.raises(dv.ValidationError) as err:
        dv.path_params(event, {"agency_id": dv.agency_id, "letter": dv.letter})
    assert err.value.fields == ["letter"]
    assert dv.path_params({"pathParameters": {"agency_id": "AG-TUN-03"}}, {"agency_id": dv.agency_id}) == \
        {"agency_id": "AG-TUN-03"}
