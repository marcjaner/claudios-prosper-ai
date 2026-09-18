# Prosper — Platform API

**Version:** `0.1.0`

[Download OpenAPI specification](https://hackspain.getprosperapp.com/api/openapi.json)

## Authentication

Protected endpoints require:

`TeamApiKey`

---

# Status

## Health

`GET /api/v1/health`

Checks the API health status.

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    null

---

# Submission

## Register Patient

`POST /api/v1/submit/register`

Put a caller who is not on file into the record.

### Authorization

`TeamApiKey`

### Request body

**Content-Type:** `application/json`

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `call_id` | Yes | `string` | The `callSid` of the `start` message that opened the call. Never minted by the team. |
| `given_name` | Yes | `string` | Given name. Must be non-empty. |
| `first_surname` | Yes | `string` | First surname. Must be non-empty. |
| `second_surname` | Yes | `string` | Second surname. Must be non-empty. |
| `national_id` | Yes | `string` | DNI or NIE. The check letter is re-derived from the digits. A mismatched letter returns `422`. |
| `date_of_birth` | Yes | `date` | Date of birth. |
| `phone` | Yes | `string` | Phone as dictated. Compared after normalization. |
| `email` | Yes | `string` | Email as dictated. Compared after normalization. |
| `insurer` | Yes | `string` | Insurance plan under which the patient is registered. |

### `insurer` values

- `sanitas`
- `adeslas`
- `dkv`
- `asisa`
- `mapfre`
- `caser`
- `cigna`
- `axa`
- `nueva_mutua`
- `privado`

### Example request

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "date_of_birth": "1988-03-14",
      "email": "ana.garcia@gmail.com",
      "first_surname": "García",
      "given_name": "Ana",
      "insurer": "adeslas",
      "national_id": "12345678Z",
      "phone": "+34612345678",
      "second_surname": "López"
    }

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `404` | No call with this `call_id` is registered to your key. |
| `410` | The submission window for this call has closed. |
| `422` | Validation error |

### Example response

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "received_at": "2026-09-18T10:41:07.512Z",
      "record": {
        "actions": []
      }
    }

---

## Book Appointment

`POST /api/v1/submit/book`

Book a slot for a patient already on file.

### Authorization

`TeamApiKey`

### Request body

**Content-Type:** `application/json`

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `call_id` | Yes | `string` | The `callSid` of the `start` message that opened the call. Never minted by the team. |
| `patient_id` | Yes | `string` | Patient ID from the directory lookup. Never use a value supplied by the caller. |
| `provider_id` | Yes | `string` | Provider who will see the patient, from the directory. |
| `location_id` | Yes | `string` | Site of the appointment, from availability. |
| `appointment_type_id` | Yes | `string` | Type of visit, from availability. |
| `slot` | Yes | `date-time` | Appointment start with an explicit timezone offset. Compared in `Europe/Madrid` to the exact minute. |
| `policy_id` | Yes | `string` | Patient plan against which the appointment is billed. |

### `policy_id` values

- `sanitas`
- `adeslas`
- `dkv`
- `asisa`
- `mapfre`
- `caser`
- `cigna`
- `axa`
- `nueva_mutua`
- `privado`

A patient may hold two plans, and only one may cover the requested appointment.

### Example request

    {
      "appointment_type_id": "review",
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "location_id": "sur",
      "patient_id": "P00042",
      "policy_id": "sanitas",
      "provider_id": "PR05",
      "slot": "2026-09-24T16:30:00+02:00"
    }

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `404` | No call with this `call_id` is registered to your key. |
| `410` | The submission window for this call has closed. |
| `422` | Validation error |

### Example response

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "received_at": "2026-09-18T10:41:07.512Z",
      "record": {
        "actions": []
      }
    }

---

## Reschedule Appointment

`POST /api/v1/submit/reschedule`

Move an existing appointment to another slot.

### Authorization

`TeamApiKey`

### Request body

**Content-Type:** `application/json`

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `call_id` | Yes | `string` | The `callSid` of the `start` message that opened the call. Never minted by the team. |
| `appointment_id` | Yes | `string` | Existing appointment from the patient's appointment lookup. |
| `provider_id` | Yes | `string` | Provider who will see the patient, from the directory. |
| `location_id` | Yes | `string` | Site of the appointment, from availability. |
| `slot` | Yes | `date-time` | New start time with an explicit timezone offset. Compared in `Europe/Madrid` to the exact minute. |
| `policy_id` | Yes | `string` | Patient plan against which the appointment is billed. |

### `policy_id` values

- `sanitas`
- `adeslas`
- `dkv`
- `asisa`
- `mapfre`
- `caser`
- `cigna`
- `axa`
- `nueva_mutua`
- `privado`

### Example request

    {
      "appointment_id": "A000123",
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "location_id": "sur",
      "policy_id": "sanitas",
      "provider_id": "PR05",
      "slot": "2026-09-24T16:30:00+02:00"
    }

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `404` | No call with this `call_id` is registered to your key. |
| `410` | The submission window for this call has closed. |
| `422` | Validation error |

### Example response

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "received_at": "2026-09-18T10:41:07.512Z",
      "record": {
        "actions": []
      }
    }

---

## Cancel Appointment

`POST /api/v1/submit/cancel`

Cancel an existing appointment.

Two cancellations require two requests.

### Authorization

`TeamApiKey`

### Request body

**Content-Type:** `application/json`

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `call_id` | Yes | `string` | The `callSid` of the `start` message that opened the call. Never minted by the team. |
| `appointment_id` | Yes | `string` | Existing appointment from the patient's appointment lookup. |

### Example request

    {
      "appointment_id": "A000123",
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    }

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `404` | No call with this `call_id` is registered to your key. |
| `410` | The submission window for this call has closed. |
| `422` | Validation error |

### Example response

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "received_at": "2026-09-18T10:41:07.512Z",
      "record": {
        "actions": []
      }
    }

---

## No Action

`POST /api/v1/submit/no-action`

End the call without a write and record the reason.

### Authorization

`TeamApiKey`

### Request body

**Content-Type:** `application/json`

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `call_id` | Yes | `string` | The `callSid` of the `start` message that opened the call. Never minted by the team. |
| `reason` | Yes | `OutcomeReason` | Reason why the call ended without a write. |

### `reason` values

- `not_eligible_age`
- `referral_required`
- `provider_not_in_network`
- `specialty_not_covered`
- `location_not_covered`
- `insurer_referral_required`
- `allowance_exhausted`
- `provider_on_leave`
- `location_hours`
- `type_not_offered`
- `patient_history`
- `no_availability`
- `clinic_closed`
- `patient_not_found`
- `provider_not_found`
- `caller_not_authorised`
- `out_of_scope`
- `medical_emergency`

The first eleven values map directly to the clinic's published restrictions.

### Example request

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "reason": "no_availability"
    }

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `404` | No call with this `call_id` is registered to your key. |
| `410` | The submission window for this call has closed. |
| `422` | Validation error |

### Example response

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "received_at": "2026-09-18T10:41:07.512Z",
      "record": {
        "actions": []
      }
    }

---

## Escalate to Human

`POST /api/v1/submit/escalate`

Hand the call to a human and record the reason.

### Authorization

`TeamApiKey`

### Request body

**Content-Type:** `application/json`

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `call_id` | Yes | `string` | The `callSid` of the `start` message that opened the call. Never minted by the team. |
| `reason` | Yes | `OutcomeReason` | Reason for escalation. |

### `reason` values

- `not_eligible_age`
- `referral_required`
- `provider_not_in_network`
- `specialty_not_covered`
- `location_not_covered`
- `insurer_referral_required`
- `allowance_exhausted`
- `provider_on_leave`
- `location_hours`
- `type_not_offered`
- `patient_history`
- `no_availability`
- `clinic_closed`
- `patient_not_found`
- `provider_not_found`
- `caller_not_authorised`
- `out_of_scope`
- `medical_emergency`

### Example request

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "reason": "medical_emergency"
    }

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `404` | No call with this `call_id` is registered to your key. |
| `410` | The submission window for this call has closed. |
| `422` | Validation error |

### Example response

    {
      "call_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "received_at": "2026-09-18T10:41:07.512Z",
      "record": {
        "actions": []
      }
    }

---

## List Submissions

`GET /api/v1/submissions`

Returns recent submission records.

### Authorization

`TeamApiKey`

### Query parameters

| Parameter | Required | Type | Default | Constraints | Description |
| --- | --- | --- | --- | --- | --- |
| `limit` | No | `integer` | `50` | `1..200` | Number of most recent records to return. |

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `422` | Validation error |

### Example response

    {
      "submissions": [
        {}
      ]
    }

---

# Clinic

## Search Patient

`GET /api/v1/directory`

Search the patient directory.

### Authorization

`TeamApiKey`

### Query parameters

| Parameter | Required | Type | Example | Description |
| --- | --- | --- | --- | --- |
| `name` | No | `string` | `Marta Ruiz Gomez` | Full or partial name, compared after normalization. |
| `national_id` | No | `string` | `12345678Z` | DNI or NIE as dictated. |
| `phone` | No | `string` | `612345678` | Phone as dictated. Digits are compared. |
| `date_of_birth` | No | `date` | `1988-03-14` | ISO date used to distinguish patients with the same name. |

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `422` | Validation error |

### Example response

    {
      "matches": [
        {}
      ]
    }

---

## List Patient Appointments

`GET /api/v1/patients/{patient_id}/appointments`

Returns appointments for a patient.

### Authorization

`TeamApiKey`

### Path parameters

| Parameter | Required | Type | Example | Description |
| --- | --- | --- | --- | --- |
| `patient_id` | Yes | `string` | `P00042` | Patient ID from the directory lookup. |

### Query parameters

| Parameter | Required | Type | Default | Values | Description |
| --- | --- | --- | --- | --- | --- |
| `when` | No | `string` | `upcoming` | `upcoming`, `past`, `all` | Select which appointments to return. |

Only an upcoming appointment can be cancelled or rescheduled.

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `422` | Validation error |

### Example response

    {
      "appointments": [
        {}
      ]
    }

---

## Search Availability

`GET /api/v1/availability`

Search available appointment slots.

### Authorization

`TeamApiKey`

### Query parameters

| Parameter | Required | Type | Example | Description |
| --- | --- | --- | --- | --- |
| `date_from` | Yes | `date` | `2026-09-21` | First day of the search window, inclusive. |
| `date_to` | Yes | `date` | `2026-09-25` | Last day of the search window, inclusive. |
| `provider_id` | No | `string` | `PR05` | Return slots only for this provider. |
| `specialty_id` | No | `string` | `dermatology` | Return slots only for providers in this specialty. |
| `location_id` | No | `string` | `sur` | Return slots only at this location. |
| `patient_id` | No | `string` | `P00042` | Apply the patient's age, history, and plan eligibility. |
| `insurer` | No | `string[]` | `sanitas` | Return slots covered by these plans. Repeat the parameter for several plans. |

When `patient_id` is provided, returned slots already account for the patient's eligibility.

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |
| `422` | Validation error |

### Example response

    {
      "providers": [
        {}
      ],
      "appointment_type": {
        "id": "string",
        "name": "string",
        "duration_minutes": 0,
        "new_patient_requirement": "string",
        "guidance": "string"
      },
      "slots": [
        {}
      ],
      "blocked": [
        {}
      ]
    }

---

## Clinic Overview

`GET /api/v1/clinic`

Returns the whole clinic catalogue in one request, including:

- Bookable calendar window
- Standing restrictions and decline reasons
- Providers
- Specialties
- Appointment types
- Locations
- Insurance plans

### Authorization

`TeamApiKey`

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    {
      "clinic_name": "string",
      "patient_count": 0,
      "calendar": {
        "starts": "2019-08-24",
        "ends": "2019-08-24",
        "max_span_days": 0,
        "slot_minutes": 0,
        "closure_days": [],
        "appointment_count": 0
      },
      "restrictions": [
        {}
      ],
      "providers": [
        {}
      ],
      "specialties": [
        {}
      ],
      "appointment_types": [
        {}
      ],
      "locations": [
        {}
      ],
      "plans": [
        {}
      ]
    }

---

## List Providers

`GET /api/v1/providers`

Returns every provider, including:

- Specialty
- Languages
- Appointment types
- Locations
- Working times
- Accepted plans
- Leave

### Authorization

`TeamApiKey`

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    {
      "providers": [
        {}
      ]
    }

---

## List Locations

`GET /api/v1/locations`

Returns every clinic site, including:

- Address
- Opening hours
- Providers
- Covered insurance plans

### Authorization

`TeamApiKey`

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    {
      "locations": [
        {}
      ]
    }

---

## List Specialties

`GET /api/v1/specialties`

Returns every specialty, including:

- Age window
- Referral requirement
- Covered insurance plans

The specialty IDs are accepted by:

`GET /api/v1/availability?specialty_id=...`

### Authorization

`TeamApiKey`

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    {
      "specialties": [
        {}
      ]
    }

---

## List Appointment Types

`GET /api/v1/appointment-types`

Returns every appointment type and its duration and patient requirements.

The appointment type follows the patient's history and specialty, not the caller's requested type.

### Authorization

`TeamApiKey`

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    {
      "appointment_types": [
        {}
      ]
    }

---

## List Insurance Plans

`GET /api/v1/insurance-plans`

Returns every insurance plan, including:

- Covered services
- Covered locations
- Providers who accept the plan

A patient's plan on file is returned by `/directory`.

A patient may hold a second plan that does not exist in the stored data and is only revealed during the call.

### Authorization

`TeamApiKey`

### Responses

| Status | Description |
| --- | --- |
| `200` | Successful response |

### Example response

    {
      "plans": [
        {}
      ]
    }