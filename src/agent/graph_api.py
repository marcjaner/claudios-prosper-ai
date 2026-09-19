"""HTTP surface for the builder: read the graph, save the graph, list the tools."""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI, HTTPException

from .clinic_api import ClinicApi
from .graph import GRAPH_PATH, InvalidGraph, load_graph, parse_graph, save_graph
from .tools import create_clinic_tools, load_tools


def available_tools() -> list[dict[str, str]]:
    """Every tool a stage may be given, with the description the model sees.

    ``load_tools`` only inspects signatures and docstrings, so introspection
    needs no live clinic connection.
    """
    tools = load_tools(create_clinic_tools(cast(ClinicApi, None), ""))
    return [
        {"name": name, "description": tool["definition"]["function"]["description"]}
        for name, tool in tools.items()
    ]


def register_graph_api(app: FastAPI) -> None:
    @app.get("/api/graph")
    async def get_graph() -> dict[str, Any]:
        return load_graph().model_dump(by_alias=True)

    @app.put("/api/graph")
    async def put_graph(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            graph = parse_graph(payload)
            save_graph(graph)
        except InvalidGraph as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return graph.model_dump(by_alias=True)

    @app.get("/api/tools")
    async def get_tools() -> dict[str, Any]:
        return {"tools": available_tools()}

    @app.get("/api/graph/path", include_in_schema=False)
    async def graph_path() -> dict[str, str]:
        return {"path": str(GRAPH_PATH)}
