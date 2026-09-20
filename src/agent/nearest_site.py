"""Address lookup and distance-based availability using published clinic coordinates."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, time
from functools import lru_cache
from typing import Any

import httpx

from .clinic_api import ClinicApi

GEOCODER_URL = "https://www.cartociudad.es/geocoder/api/geocoder/candidates"
EARTH_RADIUS_KM = 6371.0088
ADDRESS_WORDS = frozenset(
    ["calle", "avenida", "avda", "av", "paseo", "plaza", "carretera", "camino", "ronda", "travesia", "street", "road", "de", "del", "la", "las", "el", "los", "numero", "number", "spain", "espana"]
)
NUMBER_WORDS = {
    word: str(number) for number, word in enumerate([
        "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
        "nueve", "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis",
        "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidos",
        "veintitres", "veinticuatro", "veinticinco", "veintiseis", "veintisiete",
        "veintiocho", "veintinueve", "treinta",
    ])
}


def _address_words(address: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKD", address).encode("ascii", "ignore").decode()
    normalized = re.sub(r"\b\d{5}\b", "", normalized.lower())
    numbers = list(re.finditer(r"\b\d{1,4}[a-z]?\b", normalized))
    if numbers:
        number = numbers[-1]
        normalized = normalized[:number.start()] + normalized[number.end():]
    words = re.findall(r"[a-z]+|\d+", normalized)
    return frozenset(NUMBER_WORDS.get(word, word) for word in words) - ADDRESS_WORDS


@lru_cache(maxsize=128)
def geocode_address(address: str) -> tuple[dict[str, Any], ...]:
    response = httpx.get(
        GEOCODER_URL,
        params={"q": address, "limit": 5, "provincia_filter": "Madrid"},
        headers={"User-Agent": "ClaudiosProsperAI/1.0 (HackSpain clinic demo)"},
        timeout=5.0,
    )
    response.raise_for_status()
    candidates = [
        place for place in response.json()
        if place.get("type") == "portal"
        and place.get("lat") and place.get("lng")
        and _address_words(place["address"]) == _address_words(address)
    ]
    numbers = re.findall(r"\b\d{1,4}\b", address)
    if not candidates or not numbers:
        raise ValueError("Ask for the street, number and town; the address was not resolved.")
    exact = [p for p in candidates if str(p.get("portalNumber")) == numbers[-1]]
    return tuple(exact or candidates)


def distance_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, origin)
    lat2, lon2 = map(math.radians, destination)
    half_chord = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, half_chord)))


def _rank_locations(origin: dict, locations: list[dict]) -> list[dict]:
    ranked = [
        {
            "location_id": location["id"],
            "name": location["name"],
            "address": location["address"],
            "distance_km": distance_km(
                (origin["lat"], origin["lng"]),
                (location["latitude"], location["longitude"]),
            ),
        }
        for location in locations
    ]
    return sorted(ranked, key=lambda location: location["distance_km"])


def search_filtered_availability(api: ClinicApi, filters: dict) -> dict:
    filters = dict(filters)
    language = filters.pop("provider_language", None)
    start, end = filters.pop("time_from", None), filters.pop("time_to", None)
    start_time, end_time = time.fromisoformat(start or "00:00"), time.fromisoformat(end or "23:59")
    if start_time > end_time:
        raise ValueError("time_to must be on or after time_from, using local HH:MM times.")
    result = api.search_availability(**filters)
    if language:
        eligible = {p["id"] for p in api.get_clinic()["providers"] if language in p.get("languages", [])}
        result = {
            **result,
            "slots": [slot for slot in result.get("slots", []) if slot["provider_id"] in eligible],
            "provider_language": language,
        }
    if not start and not end:
        return result
    return {
        **result,
        "slots": [
            slot for slot in result.get("slots", [])
            if start_time <= datetime.fromisoformat(slot["start_time"]).time() <= end_time
        ],
        "time_window": {"time_from": start, "time_to": end},
    }


def search_nearest_availability(api: ClinicApi, address: str, filters: dict) -> dict:
    if filters.get("location_id"):
        raise ValueError("Use near_address or location_id, not both; keep the caller's constraint.")
    catalogue = api.get_clinic()
    locations = {
        schedule["location_id"]
        for provider in catalogue["providers"]
        if (not filters.get("provider_id") or provider["id"] == filters["provider_id"])
        and (not filters.get("specialty_id") or provider["specialty_id"] == filters["specialty_id"])
        and (not filters.get("provider_language") or filters["provider_language"] in provider.get("languages", []))
        for schedule in provider["schedules"]
    }
    eligible = [location for location in catalogue["locations"] if location["id"] in locations]
    if not eligible:
        raise ValueError("No site offers this provider/specialty. Check the catalogue IDs.")
    try:
        origins = geocode_address(" ".join(address.strip().split()))
    except (httpx.HTTPError, ValueError):
        return {
            "ok": False,
            "error": "address_lookup_failed",
            "instruction": "Ask the caller to confirm the street, number and town, then retry near_address. Do not guess a site or submit no_action because an address lookup failed.",
        }
    rankings = [_rank_locations(origin, eligible) for origin in origins]
    if len({tuple(p["location_id"] for p in ranked) for ranked in rankings}) != 1:
        return {
            "ok": False,
            "error": "ambiguous_address",
            "instruction": "Ask the caller to clarify the address and town before choosing a site.",
        }
    origin = origins[0]
    ranked = rankings[0]
    checked = []
    all_blocked = []
    result: dict = {}
    selected = None
    for location in ranked:
        result = search_filtered_availability(api, {**filters, "location_id": location["location_id"]})
        slots = result.get("slots", [])
        checked.append({"location_id": location["location_id"], "slots": len(slots)})
        all_blocked.extend(result.get("blocked", []))
        if slots:
            selected = location
            break
    if selected is None:
        result = {**result, "blocked": all_blocked}
    return {
        **result,
        "nearest_site": {
            "source": "CartoCiudad (IGN/CNIG)",
            "requested_address": address,
            "resolved_address": origin["address"],
            "latitude": origin["lat"],
            "longitude": origin["lng"],
            "location": selected,
            "ranked_locations": ranked,
            "checked_locations": checked,
            "rule": "Nearest site with bookable slots for these filters, by straight-line distance to published clinic coordinates. Choose the earliest suitable returned slot at this site.",
        },
    }
