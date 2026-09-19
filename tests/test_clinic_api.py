import json

import httpx
import pytest

from agent.clinic_api import ClinicApi, ProsperApiError
from agent.clinic_models import (
    BookRequest,
    CancelRequest,
    OutcomeReason,
    OutcomeRequest,
    RegisterPatientRequest,
    RescheduleRequest,
)
from agent.tools import create_clinic_tools, load_tools


class FakeProsper:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.catalogue_calls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["X-Api-Key"] == "pk-test"

        if request.url.path == "/api/v1/clinic":
            self.catalogue_calls += 1
            return httpx.Response(200, json={"clinic_name": "Arenal"})
        if request.url.path == "/api/v1/directory":
            return httpx.Response(200, json={"matches": []})
        if request.url.path == "/api/v1/availability":
            return httpx.Response(200, json={"slots": []})
        if request.url.path.startswith("/api/v1/patients/"):
            return httpx.Response(200, json={"appointments": []})
        if request.url.path.startswith("/api/v1/submit/"):
            return httpx.Response(200, json={"record": {"actions": []}})
        return httpx.Response(404, json={"detail": "missing"})


@pytest.fixture
def prosper() -> FakeProsper:
    return FakeProsper()


@pytest.fixture
def api(prosper: FakeProsper) -> ClinicApi:
    client = httpx.Client(
        base_url="https://prosper.test",
        headers={"X-Api-Key": "pk-test"},
        transport=httpx.MockTransport(prosper.handle),
    )
    return ClinicApi("https://prosper.test", "pk-test", client=client)


def request_json(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


def test_get_clinic_is_cached_until_refreshed(api: ClinicApi, prosper: FakeProsper):
    assert api.get_clinic() == {"clinic_name": "Arenal"}
    assert api.get_clinic() == {"clinic_name": "Arenal"}
    assert prosper.catalogue_calls == 1

    api.get_clinic(refresh=True)

    assert prosper.catalogue_calls == 2


def test_read_calls_use_expected_paths_and_query_parameters(api: ClinicApi, prosper: FakeProsper):
    api.search_patients(phone="+34612345678", date_of_birth="1988-03-14")
    api.get_patient_appointments("P00042")
    api.search_availability(
        date_from="2026-09-21",
        date_to="2026-09-25",
        specialty_id="dermatology",
        patient_id="P00042",
        insurers=["sanitas", "dkv"],
    )

    directory, appointments, availability = prosper.requests
    assert directory.url.path == "/api/v1/directory"
    assert dict(directory.url.params) == {
        "phone": "+34612345678",
        "date_of_birth": "1988-03-14",
    }
    assert appointments.url.path == "/api/v1/patients/P00042/appointments"
    assert dict(appointments.url.params) == {"when": "upcoming"}
    assert availability.url.path == "/api/v1/availability"
    assert availability.url.params.get_list("insurer") == ["sanitas", "dkv"]
    assert dict(availability.url.params)["specialty_id"] == "dermatology"


def test_submission_methods_send_exact_route_and_payload(api: ClinicApi, prosper: FakeProsper):
    api.register_patient(
        RegisterPatientRequest(
            call_id="CA-1",
            given_name="Ana",
            first_surname="García",
            second_surname="López",
            national_id="12345678Z",
            date_of_birth="1988-03-14",
            phone="+34612345678",
            email="ana@example.com",
            insurer="sanitas",
        )
    )
    api.book(
        BookRequest(
            call_id="CA-1",
            patient_id="P00042",
            provider_id="PR05",
            location_id="sur",
            appointment_type_id="review",
            slot="2026-09-24T16:30:00+02:00",
            policy_id="sanitas",
        )
    )
    api.reschedule(
        RescheduleRequest(
            call_id="CA-1",
            appointment_id="A000123",
            provider_id="PR05",
            location_id="sur",
            slot="2026-09-24T16:30:00+02:00",
            policy_id="sanitas",
        )
    )
    api.cancel(CancelRequest(call_id="CA-1", appointment_id="A000123"))
    api.no_action(
        OutcomeRequest(call_id="CA-1", reason=OutcomeReason.NO_AVAILABILITY)
    )
    api.escalate(
        OutcomeRequest(call_id="CA-1", reason=OutcomeReason.MEDICAL_EMERGENCY)
    )

    assert [request.url.path for request in prosper.requests] == [
        "/api/v1/submit/register",
        "/api/v1/submit/book",
        "/api/v1/submit/reschedule",
        "/api/v1/submit/cancel",
        "/api/v1/submit/no-action",
        "/api/v1/submit/escalate",
    ]
    assert request_json(prosper.requests[0])["date_of_birth"] == "1988-03-14"
    assert request_json(prosper.requests[1])["appointment_type_id"] == "review"
    assert request_json(prosper.requests[4]) == {
        "call_id": "CA-1",
        "reason": "no_availability",
    }


def test_api_errors_preserve_status_and_response():
    client = httpx.Client(
        base_url="https://prosper.test",
        headers={"X-Api-Key": "pk-test"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(422, json={"detail": "bad request"})
        ),
    )
    api = ClinicApi("https://prosper.test", "pk-test", client=client)

    with pytest.raises(ProsperApiError) as error:
        api.search_patients(phone="not-a-phone")

    assert error.value.status_code == 422
    assert error.value.detail == {"detail": "bad request"}


def test_per_call_tools_hide_call_id_and_generate_expected_schema(api: ClinicApi):
    tools = load_tools(create_clinic_tools(api, "CA-42"))
    booking = tools["book_appointment"].parameters
    availability = tools["search_availability"].parameters

    assert "call_id" not in booking["properties"]
    assert availability["properties"]["insurers"] == {
        "type": "array",
        "items": {"type": "string"},
    }
