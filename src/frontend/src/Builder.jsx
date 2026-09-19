import { useCallback, useEffect, useState } from "react";
import {
  addEdge,
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

// Fact keys are edited as free text, so the same splitter serves every list field.
const splitKeys = (text) =>
  text.split(",").map((key) => key.trim()).filter(Boolean);

function StageNode({ id, data, selected }) {
  return (
    <div
      className={`min-w-44 rounded-lg border px-3 py-2 text-left ${
        selected ? "border-emerald-400 bg-slate-800" : "border-slate-700 bg-slate-900"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-slate-500" />
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-slate-100">{id}</span>
        {data.isEntry && (
          <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] uppercase text-emerald-400">
            entrada
          </span>
        )}
      </div>
      {data.tools.length === 0 ? (
        <p className="mt-1 text-[11px] text-slate-600">sin herramientas</p>
      ) : (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {data.tools.map((tool) => (
            <span key={tool} className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
              {tool}
            </span>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-slate-500" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

const toFlowNodes = (graph) =>
  graph.nodes.map((node) => ({
    id: node.id,
    type: "stage",
    position: node.position ?? { x: 0, y: 0 },
    data: {
      prompt: node.prompt ?? "",
      tools: node.tools ?? [],
      clears: node.clears ?? [],
      isEntry: node.id === graph.entry,
    },
  }));

const toFlowEdges = (graph) =>
  graph.edges.map((edge, index) => ({
    id: `${edge.from}->${edge.to}-${index}`,
    source: edge.from,
    target: edge.to,
    label: (edge.requires ?? []).join(", ") || "sin requisitos",
    data: { requires: edge.requires ?? [] },
    labelStyle: { fill: "#94a3b8", fontSize: 11 },
    labelBgStyle: { fill: "#0f172a" },
    style: { stroke: "#475569" },
  }));

export default function Builder() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [entry, setEntry] = useState("");
  const [tools, setTools] = useState([]);
  const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    Promise.all([
      fetch("/api/graph").then((response) => response.json()),
      fetch("/api/tools").then((response) => response.json()),
    ]).then(([graph, catalogue]) => {
      setNodes(toFlowNodes(graph));
      setEdges(toFlowEdges(graph));
      setEntry(graph.entry);
      setTools(catalogue.tools);
    });
  }, [setNodes, setEdges]);

  const onConnect = useCallback(
    (connection) =>
      setEdges((current) =>
        addEdge(
          {
            ...connection,
            label: "sin requisitos",
            data: { requires: [] },
            labelStyle: { fill: "#94a3b8", fontSize: 11 },
            labelBgStyle: { fill: "#0f172a" },
            style: { stroke: "#475569" },
          },
          current,
        ),
      ),
    [setEdges],
  );

  const patchNode = (id, changes) =>
    setNodes((current) =>
      current.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...changes } } : node)),
    );

  const patchEdge = (id, requires) =>
    setEdges((current) =>
      current.map((edge) =>
        edge.id === id
          ? { ...edge, data: { requires }, label: requires.join(", ") || "sin requisitos" }
          : edge,
      ),
    );

  const addStage = () => {
    const id = `etapa_${nodes.length + 1}`;
    setNodes((current) => [
      ...current,
      {
        id,
        type: "stage",
        position: { x: 80 + current.length * 40, y: 80 + current.length * 60 },
        data: { prompt: "", tools: [], clears: [], isEntry: current.length === 0 },
      },
    ]);
    if (!entry) setEntry(id);
  };

  const removeSelected = () => {
    if (!selected) return;
    if (selected.kind === "node") {
      setNodes((current) => current.filter((node) => node.id !== selected.id));
      setEdges((current) =>
        current.filter((edge) => edge.source !== selected.id && edge.target !== selected.id),
      );
    } else {
      setEdges((current) => current.filter((edge) => edge.id !== selected.id));
    }
    setSelected(null);
  };

  const makeEntry = (id) => {
    setEntry(id);
    setNodes((current) =>
      current.map((node) => ({ ...node, data: { ...node.data, isEntry: node.id === id } })),
    );
  };

  const save = async () => {
    const payload = {
      entry,
      nodes: nodes.map((node) => ({
        id: node.id,
        prompt: node.data.prompt,
        tools: node.data.tools,
        clears: node.data.clears,
        position: node.position,
      })),
      edges: edges.map((edge) => ({
        from: edge.source,
        to: edge.target,
        requires: edge.data?.requires ?? [],
      })),
    };
    const response = await fetch("/api/graph", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    setStatus(response.ok ? "guardado" : `rechazado: ${body.detail}`);
  };

  const node = selected?.kind === "node" ? nodes.find((item) => item.id === selected.id) : null;
  const edge = selected?.kind === "edge" ? edges.find((item) => item.id === selected.id) : null;

  return (
    <div className="flex h-[calc(100vh-73px)]">
      <div className="relative flex-1">
        <div className="absolute left-4 top-4 z-10 flex items-center gap-2">
          <button
            onClick={addStage}
            className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700"
          >
            Añadir etapa
          </button>
          <button
            onClick={removeSelected}
            disabled={!selected}
            className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-700 disabled:opacity-40"
          >
            Eliminar
          </button>
          <button
            onClick={save}
            className="rounded-md bg-emerald-500/15 px-3 py-1.5 text-sm text-emerald-400 hover:bg-emerald-500/25"
          >
            Guardar
          </button>
          {status && (
            <span
              className={`text-xs ${status === "guardado" ? "text-emerald-400" : "text-rose-400"}`}
            >
              {status}
            </span>
          )}
        </div>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={(_, clicked) => setSelected({ kind: "node", id: clicked.id })}
          onEdgeClick={(_, clicked) => setSelected({ kind: "edge", id: clicked.id })}
          onPaneClick={() => setSelected(null)}
          fitView
          colorMode="dark"
        >
          <Background color="#1e293b" />
          <Controls />
        </ReactFlow>
      </div>

      <aside className="w-96 overflow-y-auto border-l border-slate-800 bg-slate-950 p-4">
        {!selected && (
          <p className="text-sm text-slate-500">
            Selecciona una etapa o una transición. Arrastra de un conector a otro para crear una
            transición.
          </p>
        )}

        {node && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-medium text-slate-200">{node.id}</h2>
              {entry !== node.id && (
                <button
                  onClick={() => makeEntry(node.id)}
                  className="text-xs text-slate-500 hover:text-emerald-400"
                >
                  marcar como entrada
                </button>
              )}
            </div>

            <label className="block">
              <span className="text-xs uppercase tracking-wide text-slate-500">Instrucciones</span>
              <textarea
                value={node.data.prompt}
                onChange={(event) => patchNode(node.id, { prompt: event.target.value })}
                rows={8}
                className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 p-2 text-sm text-slate-200"
              />
            </label>

            <div>
              <span className="text-xs uppercase tracking-wide text-slate-500">Herramientas</span>
              <div className="mt-1 space-y-1">
                {tools.map((tool) => (
                  <label key={tool.name} className="flex items-start gap-2 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={node.data.tools.includes(tool.name)}
                      onChange={(event) =>
                        patchNode(node.id, {
                          tools: event.target.checked
                            ? [...node.data.tools, tool.name]
                            : node.data.tools.filter((name) => name !== tool.name),
                        })
                      }
                      className="mt-1"
                    />
                    <span>
                      <span className="font-mono text-xs">{tool.name}</span>
                      <span className="block text-[11px] text-slate-600">{tool.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <label className="block">
              <span className="text-xs uppercase tracking-wide text-slate-500">
                Olvida al entrar
              </span>
              <input
                value={node.data.clears.join(", ")}
                onChange={(event) => patchNode(node.id, { clears: splitKeys(event.target.value) })}
                placeholder="p. ej. slot, policy_id"
                className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 p-2 font-mono text-sm text-slate-200"
              />
            </label>
          </div>
        )}

        {edge && (
          <div className="space-y-4">
            <h2 className="font-medium text-slate-200">
              {edge.source} <span className="text-slate-600">→</span> {edge.target}
            </h2>
            <label className="block">
              <span className="text-xs uppercase tracking-wide text-slate-500">
                Datos necesarios
              </span>
              <input
                value={(edge.data?.requires ?? []).join(", ")}
                onChange={(event) => patchEdge(edge.id, splitKeys(event.target.value))}
                placeholder="p. ej. patient_id"
                className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 p-2 font-mono text-sm text-slate-200"
              />
              <span className="mt-1 block text-[11px] text-slate-600">
                El agente no puede pasar a {edge.target} hasta que haya registrado estos datos.
              </span>
            </label>
          </div>
        )}
      </aside>
    </div>
  );
}
