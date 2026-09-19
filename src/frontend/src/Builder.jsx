import { useCallback, useEffect, useRef, useState } from "react";
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

// Whole tool lists made nodes metres wide; the side panel shows the rest.
const CHIP_LIMIT = 4;

// Fact keys are edited as free text, so the same splitter serves every list field.
// It runs when editing finishes, never per keystroke: splitting as you type eats
// the comma you just pressed and silently welds two keys into one.
const splitKeys = (text) =>
  text.split(",").map((key) => key.trim()).filter(Boolean);

// React Flow derives edge ids from the endpoints, so renaming a stage and reusing
// its old name produces two different edges sharing one id — and deleting one
// would delete both. These ids are independent of the names.
let edgeSequence = 0;
const nextEdgeId = () => `edge_${(edgeSequence += 1)}`;

function StageNode({ id, data, selected }) {
  return (
    <div
      className={`w-52 rounded-lg border px-3 py-2 text-left ${
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
          {data.tools.slice(0, CHIP_LIMIT).map((tool) => (
            <span
              key={tool}
              title={tool}
              className="max-w-full truncate rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400"
            >
              {tool}
            </span>
          ))}
          {data.tools.length > CHIP_LIMIT && (
            <span className="px-1 py-0.5 text-[10px] text-slate-600">
              +{data.tools.length - CHIP_LIMIT}
            </span>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-slate-500" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

function KeyList({ value, draft, setDraft, onCommit, placeholder }) {
  return (
    <input
      value={draft ?? value.join(", ")}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={(event) => {
        setDraft(null);
        onCommit(splitKeys(event.target.value));
      }}
      onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()}
      placeholder={placeholder}
      className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 p-2 font-mono text-sm text-slate-200"
    />
  );
}

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
  graph.edges.map((edge) => ({
    id: nextEdgeId(),
    source: edge.from,
    target: edge.to,
    // No requirement needs no words; an unlabelled arrow already says it.
    label: (edge.requires ?? []).join(", "),
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
  // Renaming on every keystroke would forbid clearing the field to retype.
  const [draftId, setDraftId] = useState(null);
  const [draftKeys, setDraftKeys] = useState(null);

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
            id: nextEdgeId(),
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
          ? { ...edge, data: { requires }, label: requires.join(", ") }
          : edge,
      ),
    );

  const renameStage = (from, to) => {
    const trimmed = to.trim();
    setDraftId(null);
    if (!trimmed || trimmed === from || nodes.some((node) => node.id === trimmed)) return;
    setNodes((current) =>
      current.map((node) => (node.id === from ? { ...node, id: trimmed } : node)),
    );
    setEdges((current) =>
      current.map((edge) => ({
        ...edge,
        source: edge.source === from ? trimmed : edge.source,
        target: edge.target === from ? trimmed : edge.target,
      })),
    );
    if (entry === from) setEntry(trimmed);
    setSelected({ kind: "node", id: trimmed });
  };

  const addStage = () => {
    const taken = new Set(nodes.map((node) => node.id));
    let index = nodes.length + 1;
    while (taken.has(`etapa_${index}`)) index += 1;
    const id = `etapa_${index}`;
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
          onNodeClick={(_, clicked) => {
            setDraftId(null);
            setDraftKeys(null);
            setSelected({ kind: "node", id: clicked.id });
          }}
          onEdgeClick={(_, clicked) => {
            setDraftKeys(null);
            setSelected({ kind: "edge", id: clicked.id });
          }}
          onPaneClick={() => setSelected(null)}
          fitView
          fitViewOptions={{ padding: 0.25 }}
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
            <div className="flex items-center justify-between gap-2">
              <input
                value={draftId ?? node.id}
                onChange={(event) => setDraftId(event.target.value)}
                onBlur={(event) => renameStage(node.id, event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()}
                className="min-w-0 flex-1 rounded-md border border-slate-800 bg-slate-900 px-2 py-1 font-mono text-sm text-slate-100"
              />
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
              <KeyList
                value={node.data.clears}
                draft={draftKeys}
                setDraft={setDraftKeys}
                onCommit={(keys) => patchNode(node.id, { clears: keys })}
                placeholder="p. ej. slot, policy_id"
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
              <KeyList
                value={edge.data?.requires ?? []}
                draft={draftKeys}
                setDraft={setDraftKeys}
                onCommit={(keys) => patchEdge(edge.id, keys)}
                placeholder="p. ej. patient_id"
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
