"""Small synchronous wrapper around Prosper's read-only clinic API."""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from typing import Any

import httpx

from .clinic_models import (
    BookRequest,
    CancelRequest,
    OutcomeRequest,
    RegisterPatientRequest,
    RescheduleRequest,
)

MAX_CONCURRENT_REQUESTS = 5


class ProsperApiError(RuntimeError):
    """A Prosper response that the caller needs to handle explicitly."""

    def __init__(self, status_code: int, detail: object) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Prosper API returned {status_code}: {detail}")


class ClinicApi:
    """Call Prosper endpoints and retain the static clinic catalogue in memory."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
        close_client: bool = True,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=timeout_seconds,
            limits=httpx.Limits(
                max_connections=MAX_CONCURRENT_REQUESTS,
                max_keepalive_connections=MAX_CONCURRENT_REQUESTS,
            ),
        )
        self._close_client = close_client
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self._catalogue_lock = threading.Lock()
        self._catalogue: dict[str, Any] | None = None

    @classmethod
    def from_environment(cls) -> ClinicApi:
        base_url = os.getenv("PLATFORM_API_BASE_URL")
        api_key = os.getenv("PLATFORM_API_KEY")
        if not base_url or not api_key:
            raise RuntimeError(
                "Set PLATFORM_API_BASE_URL and PLATFORM_API_KEY before calling Prosper."
            )
        return _shared_environment_api(base_url, api_key)

    def close(self) -> None:
        if self._close_client:
            self._client.close()

    def health(self) -> Any:
        return self._get("/api/v1/health")

    def get_clinic(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._catalogue_lock:
            if self._catalogue is None or refresh:
                self._catalogue = self._get("/api/v1/clinic")
            return self._catalogue

    def search_patients(
        self,
        *,
        name: str | None = None,
        national_id: str | None = None,
        phone: str | None = None,
        date_of_birth: str | None = None,
    ) -> dict[str, Any]:
        return self._get(
            "/api/v1/directory",
            params=self._without_none(
                name=name,
                national_id=national_id,
                phone=phone,
                date_of_birth=date_of_birth,
            ),
        )

    def get_patient_appointments(
        self, patient_id: str, *, when: str = "upcoming"
    ) -> dict[str, Any]:
        return self._get(
            f"/api/v1/patients/{patient_id}/appointments", params={"when": when}
        )

    def search_availability(
        self,
        *,
        date_from: str,
        date_to: str,
        provider_id: str | None = None,
        specialty_id: str | None = None,
        location_id: str | None = None,
        patient_id: str | None = None,
        insurers: list[str] | None = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("date_from", date_from),
            ("date_to", date_to),
        ]
        params.extend(
            (key, value)
            for key, value in self._without_none(
                provider_id=provider_id,
                specialty_id=specialty_id,
                location_id=location_id,
                patient_id=patient_id,
            ).items()
        )
        params.extend(("insurer", insurer) for insurer in insurers or [])
        return self._get("/api/v1/availability", params=tuple(params))

    def get_submissions(self, *, limit: int = 50) -> dict[str, Any]:
        return self._get("/api/v1/submissions", params={"limit": str(limit)})

    def register_patient(self, request: RegisterPatientRequest) -> dict[str, Any]:
        return self._post("/api/v1/submit/register", request)

    def book(self, request: BookRequest) -> dict[str, Any]:
        return self._post("/api/v1/submit/book", request)

    def reschedule(self, request: RescheduleRequest) -> dict[str, Any]:
        return self._post("/api/v1/submit/reschedule", request)

    def cancel(self, request: CancelRequest) -> dict[str, Any]:
        return self._post("/api/v1/submit/cancel", request)

    def no_action(self, request: OutcomeRequest) -> dict[str, Any]:
        return self._post("/api/v1/submit/no-action", request)

    def escalate(self, request: OutcomeRequest) -> dict[str, Any]:
        return self._post("/api/v1/submit/escalate", request)

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str]
        | tuple[tuple[str, str | float | None], ...]
        | None = None,
    ) -> dict[str, Any]:
        query = httpx.QueryParams(params) if params is not None else None
        with self._request_slots:
            response = self._client.get(path, params=query)
        return self._response_json(response)

    def _post(self, path: str, request: Any) -> dict[str, Any]:
        with self._request_slots:
            response = self._client.post(path, json=request.model_dump(mode="json"))
        return self._response_json(response)

    @staticmethod
    def _without_none(**values: str | None) -> dict[str, str]:
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        if response.is_error:
            raise ProsperApiError(response.status_code, payload)
        if not isinstance(payload, dict):
            return {"data": payload}
        return payload


@lru_cache(maxsize=1)
def _shared_environment_api(base_url: str, api_key: str) -> ClinicApi:
    return ClinicApi(base_url, api_key, close_client=False)
