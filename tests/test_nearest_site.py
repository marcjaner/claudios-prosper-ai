import copy

import httpx
import pytest

from agent import nearest_site
from agent.nearest_site import distance_km, geocode_address, search_nearest_availability
from agent.tools import ClinicTools, create_clinic_tools, load_tools

LOCATIONS = [
    {"id": "centro", "name": "Arenal Centro", "address": "Arenal 12", "latitude": 40.4178, "longitude": -3.7075},
    {"id": "norte", "name": "Arenal Norte", "address": "Alberto Alcocer 24", "latitude": 40.4645, "longitude": -3.6836},
    {"id": "sur", "name": "Arenal Sur", "address": "Ciudades 8", "latitude": 40.305, "longitude": -3.7327},
]
FILTERS = {"date_from": "2026-09-21", "date_to": "2026-10-04", "specialty_id": "general_practice", "patient_id": "P1", "insurers": ["mapfre"]}


class FakeClinic:
    def __init__(self, available=("centro", "norte", "sur")):
        self.available = available
        self.calls = []

    def get_clinic(self):
        return {
            "locations": copy.deepcopy(LOCATIONS),
            "providers": [
                {"id": "GP", "specialty_id": "general_practice", "schedules": [{"location_id": site["id"]} for site in LOCATIONS]},
                {"id": "GYN", "specialty_id": "gynaecology", "schedules": [{"location_id": "centro"}]},
            ],
        }

    def search_availability(self, **filters):
        self.calls.append(filters)
        site = filters["location_id"]
        return {
            "slots": [{"location_id": site, "start_time": "2026-09-25T09:00:00+02:00"}] if site in self.available else [],
            "blocked": [] if site in self.available else [{"reason": "location_not_covered", "location_id": site}],
        }


@pytest.fixture
def northern_origin(monkeypatch):
    origin = {"address": "AVENIDA ALBERTO ALCOCER 25, Madrid", "lat": 40.459, "lng": -3.684}
    monkeypatch.setattr(nearest_site, "geocode_address", lambda _: (origin,))


def test_distance_uses_latitude_longitude_in_correct_order():
    assert distance_km((40.4178, -3.7075), (40.4178, -3.7075)) == 0
    assert 5 < distance_km((40.4178, -3.7075), (40.4645, -3.6836)) < 6


def test_nearest_site_is_selected_before_earlier_slots_elsewhere(northern_origin):
    api = FakeClinic()
    result = search_nearest_availability(api, "Alberto Alcocer 25, Madrid", FILTERS)
    assert result["nearest_site"]["location"]["location_id"] == "norte"
    assert api.calls == [{**FILTERS, "location_id": "norte"}]


def test_nearest_site_without_requested_specialty_is_excluded(northern_origin):
    api = FakeClinic()
    result = search_nearest_availability(api, "Alberto Alcocer 25, Madrid", {**FILTERS, "specialty_id": "gynaecology"})
    assert result["nearest_site"]["location"]["location_id"] == "centro"
    assert [c["location_id"] for c in api.calls] == ["centro"]


def test_closer_site_blocked_for_patient_is_skipped(northern_origin):
    api = FakeClinic(available=("centro", "sur"))
    result = search_nearest_availability(api, "Alberto Alcocer 25, Madrid", FILTERS)
    assert result["nearest_site"]["location"]["location_id"] == "centro"
    assert [c["location_id"] for c in api.calls] == ["norte", "centro"]


def test_time_constraint_is_applied_before_choosing_nearest_site(northern_origin):
    class AfternoonClinic(FakeClinic):
        def search_availability(self, **filters):
            result = super().search_availability(**filters)
            if filters["location_id"] == "centro":
                result["slots"][0]["start_time"] = "2026-09-25T15:00:00+02:00"
            return result
    api = AfternoonClinic()
    result = search_nearest_availability(api, "Alberto Alcocer 25, Madrid", {**FILTERS, "time_from": "14:00"})
    assert result["nearest_site"]["location"]["location_id"] == "centro"
    assert [c["location_id"] for c in api.calls] == ["norte", "centro"]
    assert all("time_from" not in c for c in api.calls)


def test_no_slots_retains_reasons_from_all_sites(northern_origin):
    result = search_nearest_availability(FakeClinic(available=()), "Alberto Alcocer 25, Madrid", FILTERS)
    assert result["slots"] == []
    assert result["nearest_site"]["location"] is None
    assert {b["location_id"] for b in result["blocked"]} == {"norte", "centro", "sur"}


def test_explicit_site_cannot_be_silently_overridden():
    with pytest.raises(ValueError, match="not both"):
        search_nearest_availability(FakeClinic(), "Somewhere", {**FILTERS, "location_id": "sur"})


def test_unresolved_address_does_not_become_no_availability(monkeypatch):
    def fail(_):
        raise ValueError("No match")
    monkeypatch.setattr(nearest_site, "geocode_address", fail)
    api = FakeClinic()
    result = search_nearest_availability(api, "Bad address", FILTERS)
    assert result["ok"] is False
    assert "slots" not in result
    assert api.calls == []


def test_inconsistent_nearby_matches_require_clarification(monkeypatch):
    monkeypatch.setattr(nearest_site, "geocode_address", lambda _: tuple(
        {"address": "Ambiguous", "lat": location["latitude"], "lng": location["longitude"]}
        for location in LOCATIONS[:2]
    ))
    api = FakeClinic()
    result = search_nearest_availability(api, "Ambiguous", FILTERS)
    assert result["error"] == "ambiguous_address"
    assert api.calls == []


def test_geocoder_matches_street_and_town_and_prefers_exact_number(monkeypatch):
    places = [
        {"type": "portal", "address": "CALLE ARENAL DE MAUDES 12, Madrid", "lat": 40.46, "lng": -3.68, "portalNumber": 12},
        {"type": "portal", "address": "CALLE ARENAL 10, Madrid", "lat": 40.41, "lng": -3.7, "portalNumber": 10},
        {"type": "portal", "address": "CALLE ARENAL 12, Madrid", "lat": 40.417, "lng": -3.706, "portalNumber": 12},
    ]
    requests = []
    def get(url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(200, json=places, request=httpx.Request("GET", url))
    monkeypatch.setattr(nearest_site.httpx, "get", get)
    geocode_address.cache_clear()
    assert geocode_address("Calle del Arenal 12, Madrid") == (places[2],)
    assert geocode_address("Calle del Arenal 12, Madrid") == (places[2],)
    assert len(requests) == 1
    assert "X-Api-Key" not in requests[0]["headers"]
    geocode_address.cache_clear()


def test_spelled_street_number_matches_digits_without_losing_street_identity():
    assert nearest_site._address_words("Calle 2 de Mayo 12, Alcobendas") == nearest_site._address_words("CALLE DOS DE MAYO 12, Alcobendas")
    assert nearest_site._address_words("Calle 3 de Mayo 12, Alcobendas") != nearest_site._address_words("CALLE DOS DE MAYO 12, Alcobendas")
    assert nearest_site._address_words("Calle del Arenal 12, 28013 Madrid") == nearest_site._address_words("CALLE ARENAL 12, Madrid")


def test_schema_and_tool_keep_normal_availability_unchanged(northern_origin):
    api = FakeClinic()
    tools = load_tools(create_clinic_tools(api, "no-submission"))
    assert "near_address" in tools["search_availability"].parameters["properties"]
    caller = ClinicTools(api, "no-submission")
    caller.search_availability(**FILTERS, location_id="sur")
    assert api.calls[-1]["location_id"] == "sur"
    result = caller.search_availability(**FILTERS, near_address="Alberto Alcocer 25, Madrid")
    assert result["nearest_site"]["location"]["location_id"] == "norte"


def test_requested_doctor_language_filters_slots_without_reaching_ehr():
    class LanguageClinic:
        def get_clinic(self):
            return {'providers': [
                {'id': 'PR01', 'languages': ['ca', 'es', 'en']},
                {'id': 'PR03', 'languages': ['es', 'en']},
            ]}

        def search_availability(self, **filters):
            assert 'provider_language' not in filters
            return {'slots': [
                {'provider_id': 'PR03', 'start_time': '2026-09-21T09:00:00+02:00'},
                {'provider_id': 'PR01', 'start_time': '2026-09-21T09:15:00+02:00'},
            ]}

    result = nearest_site.search_filtered_availability(LanguageClinic(), {**FILTERS, 'provider_language': 'ca'})
    assert [slot['provider_id'] for slot in result['slots']] == ['PR01']
