import asyncio
from typing import cast

from agent.graph import parse_graph
from agent.llm import LLMClient, StructuredCompletion, Usage
from agent.prompt_review import (
    FindingSource,
    PromptRevision,
    ReviewFinding,
    ReviewReport,
    deterministic_findings,
    improve_prompt,
    validate_graph,
)

GRAPH = {
    "entry": "identify",
    "system": "Never invent an appointment.",
    "nodes": [
        {
            "id": "identify",
            "prompt": "Call book_appointment immediately.",
            "tools": ["search_patients"],
        },
        {
            "id": "book",
            "prompt": "Book a returned slot.",
            "tools": ["book_appointment"],
        },
    ],
    "edges": [
        {"from": "identify", "to": "book", "requires": ["patient_id"]},
    ],
}


class FakeReviewClient:
    def __init__(self):
        self.prompts = []

    def complete_structured(self, prompt, schema, **_kwargs):
        self.prompts.append(prompt)
        if schema is PromptRevision:
            data = PromptRevision(
                revised_prompt="Identify the patient without inventing an appointment.",
                changes=["Clarified the instruction."],
            )
        else:
            data = ReviewReport(
                findings=[
                    ReviewFinding(
                        severity="suggestion",
                        title="Repeated rule",
                        message="The booking rule is repeated.",
                        sources=[FindingSource(kind="system", id="system")],
                    )
                ]
            )
        return StructuredCompletion(data=data, usage=Usage())


def test_deterministic_review_finds_a_tool_missing_from_the_stage():
    findings = deterministic_findings(parse_graph(GRAPH), "identify")

    unavailable = next(
        item for item in findings if item.title == "Acción no disponible"
    )
    assert "book_appointment" in unavailable.message
    assert unavailable.sources[0].id == "identify"


def test_prompt_improvement_preserves_the_original_and_runtime_context():
    client = FakeReviewClient()

    revision = asyncio.run(
        improve_prompt(
            parse_graph(GRAPH),
            stage_id="identify",
            client=cast(LLMClient, client),
        )
    )

    assert revision.revised_prompt.startswith("Identify")
    assert "Call book_appointment immediately." in client.prompts[0]
    assert "system + current stage only" in client.prompts[0]


def test_ai_validation_is_combined_with_deterministic_findings():
    client = FakeReviewClient()

    report = asyncio.run(
        validate_graph(
            parse_graph(GRAPH),
            scope="stage",
            stage_id="identify",
            client=cast(LLMClient, client),
        )
    )

    assert {item.title for item in report.findings} == {
        "Acción no disponible",
        "Repeated rule",
    }
    assert "SOLO la etapa actual" in client.prompts[0]


def test_builder_review_endpoints_return_structured_results(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import agent.graph_api as api_module

    async def fake_improve(_graph, *, stage_id):
        assert stage_id == "identify"
        return PromptRevision(revised_prompt="Identify the patient.")

    async def fake_validate(_graph, *, scope, stage_id):
        assert (scope, stage_id) == ("stage", "identify")
        return ReviewReport(findings=[])

    monkeypatch.setattr(api_module, "improve_prompt", fake_improve)
    monkeypatch.setattr(api_module, "validate_graph", fake_validate)
    app = FastAPI()
    api_module.register_graph_api(app)
    client = TestClient(app)

    improved = client.post(
        "/api/prompts/improve",
        json={"graph": GRAPH, "stage_id": "identify"},
    )
    validated = client.post(
        "/api/graph/validate",
        json={"graph": GRAPH, "scope": "stage", "stage_id": "identify"},
    )

    assert improved.status_code == 200
    assert improved.json()["revised_prompt"] == "Identify the patient."
    assert validated.status_code == 200
    assert validated.json() == {"findings": []}
