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

// Nurse-facing names for the agent's tools. The canvas and the tool list show
// these; anything not mapped falls back to the raw function name.
const TOOL_LABELS = {
  search_patients: "Buscar paciente",
  register_patient: "Registrar paciente",
  get_clinic_catalogue: "Consultar servicios",
  get_patient_appointments: "Ver citas del paciente",
  search_availability: "Buscar disponibilidad",
  book_appointment: "Reservar cita",
  reschedule_appointment: "Cambiar cita",
  cancel_appointment: "Cancelar cita",
  escalate_to_human: "Pasar a recepción",
  submit_no_action: "Sin acción",
};

// Plumbing the agent needs but a nurse never reasons about: kept in the
// advanced tool list, hidden from the at-a-glance canvas card.
const CANVAS_HIDDEN_TOOLS = new Set(["submit_no_action"]);
const CANVAS_CHIP_LIMIT = 3;

const toolLabel = (name) => TOOL_LABELS[name] ?? name;

// Stage icons are guessed from the (user-editable, free-text) stage name, so
// this is best-effort with a generic fallback — never a hard dependency.
const STAGE_ICONS = {
  patient: (
    <>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
    </>
  ),
  register: (
    <>
      <circle cx="10" cy="8" r="3.5" />
      <path d="M4 20a6.5 6.5 0 0 1 10.5-5.1" />
      <path d="M18 13.5v6M15 16.5h6" />
    </>
  ),
  calendar: (
    <>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4M16 3v4M4 10h16" />
    </>
  ),
  reschedule: (
    <>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4M16 3v4M4 10h16" />
      <path d="M15 14.5l2.5 2.5" />
    </>
  ),
  cancel: (
    <>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4M16 3v4M4 10h16" />
      <path d="M10 14.5l4 4M14 14.5l-4 4" />
    </>
  ),
  phone: <path d="M4 5c0 8 7 15 15 15v-3.4l-4-1.4-1.8 1.8a12 12 0 0 1-6.2-6.2L8.8 9 7.4 5H4z" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11.5v4.5M12 8h.01" />
    </>
  ),
  step: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M8.5 12l2.5 2.5 4.5-5" />
    </>
  ),
};

// First keyword hit wins, so the more specific stages are listed first.
const ICON_KEYWORDS = [
  [["registr", "alta", "nuevo", "nueva"], "register"],
  [["identif", "buscar", "paciente", "quien"], "patient"],
  [["cancel", "anular", "baja"], "cancel"],
  [["modific", "cambi", "reprogram", "reschedul"], "reschedule"],
  [["reserv", "cita", "agenda", "book"], "calendar"],
  [["atend", "llam", "recep", "telefon", "escal"], "phone"],
  [["consult", "inform", "servic", "catal", "precio"], "info"],
];

const iconFor = (name) => {
  const key = name.toLowerCase();
  for (const [words, icon] of ICON_KEYWORDS) {
    if (words.some((word) => key.includes(word))) return STAGE_ICONS[icon];
  }
  return STAGE_ICONS.step;
};

function StageIcon({ name }) {
  return (
    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-600">
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4 fill-none stroke-current"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {iconFor(name)}
      </svg>
    </span>
  );
}

function StageNode({ id, data, selected }) {
  const chips = data.tools.filter((tool) => !CANVAS_HIDDEN_TOOLS.has(tool));
  const shown = chips.slice(0, CANVAS_CHIP_LIMIT);
  const extra = chips.length - shown.length;
  return (
    <div
      className={`min-w-52 max-w-64 rounded-xl border bg-white px-3 py-2.5 text-left shadow-[0_1px_2px_rgba(15,23,42,0.03)] ${
        selected ? "border-emerald-400 ring-1 ring-emerald-200" : "border-slate-200"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-emerald-500" />
      <div className="flex items-center gap-2">
        <StageIcon name={id} />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900">{id}</span>
        {data.isEntry && (
          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">
            entrada
          </span>
        )}
      </div>
      {chips.length === 0 ? (
        <p className="mt-2 text-[11px] text-slate-400">Sin acciones</p>
      ) : (
        <div className="mt-2 flex flex-wrap gap-1">
          {shown.map((tool) => (
            <span key={tool} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
              {toolLabel(tool)}
            </span>
          ))}
          {extra > 0 && (
            <span className="rounded-full bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-400">
              +{extra} más
            </span>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-emerald-500" />
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
      className="clinic-control mt-1 w-full font-mono"
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

// A transition only gets a label when it actually gates on data. The common
// "no requirement" case stays unlabelled — otherwise every edge carries a "sin
// requisitos" pill and, where edges cross, they stack unreadably on top of each
// other. The requirement itself is always editable from the edge inspector.
const edgeLabel = (requires) => (requires.length ? requires.join(", ") : undefined);

const EDGE_VISUAL = {
  labelStyle: { fill: "#475569", fontSize: 11, fontWeight: 600 },
  labelBgStyle: { fill: "#ffffff", stroke: "#e2e8f0" },
  labelBgPadding: [8, 4],
  labelBgBorderRadius: 8,
  style: { stroke: "#94a3b8" },
};

const toFlowEdges = (graph) =>
  graph.edges.map((edge) => {
    const requires = edge.requires ?? [];
    return {
      id: nextEdgeId(),
      source: edge.from,
      target: edge.to,
      label: edgeLabel(requires),
      data: { requires },
      ...EDGE_VISUAL,
    };
  });

export default function Builder() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [entry, setEntry] = useState("");
  const [system, setSystem] = useState("");
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
      setSystem(graph.system ?? "");
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
            label: edgeLabel([]),
            data: { requires: [] },
            ...EDGE_VISUAL,
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
          ? { ...edge, data: { requires }, label: edgeLabel(requires) }
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
      system,
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
    <div className="flex h-[calc(100vh-77px)]">
      <div className="relative flex-1">
        <div className="absolute left-4 top-4 z-10 flex items-center gap-2">
          <button
            onClick={addStage}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:border-slate-300"
          >
            Añadir etapa
          </button>
          <button
            onClick={removeSelected}
            disabled={!selected}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 shadow-sm transition-colors hover:border-slate-300 disabled:opacity-40"
          >
            Eliminar
          </button>
          <button
            onClick={save}
            className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700"
          >
            Guardar
          </button>
          {status && (
            <span
              className={`text-xs font-medium ${status === "guardado" ? "text-emerald-600" : "text-rose-600"}`}
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
          colorMode="light"
        >
          <Background color="#cbd5e1" />
          <Controls />
        </ReactFlow>
      </div>

      <aside className="w-96 overflow-y-auto border-l border-slate-200 bg-white p-5">
        {!selected && (
          <div className="space-y-4">
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Instrucciones generales
              </span>
              <textarea
                id="system-prompt"
                value={system}
                onChange={(event) => setSystem(event.target.value)}
                rows={18}
                placeholder="Cómo habla el agente, qué puede afirmar y de dónde salen los identificadores."
                className="clinic-control mt-1 w-full"
              />
              <span className="mt-1 block text-[11px] text-slate-400">
                Va delante de las instrucciones de cada etapa, en todas las llamadas. Las etapas
                dicen qué hacer y cuándo; esto dice cómo.
              </span>
            </label>
            <p className="border-t border-slate-200 pt-4 text-sm text-slate-500">
              Selecciona una etapa o una transición para editarla. Arrastra de un conector a otro
              para crear una transición.
            </p>
          </div>
        )}

        {node && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
              <input
                value={draftId ?? node.id}
                onChange={(event) => setDraftId(event.target.value)}
                onBlur={(event) => renameStage(node.id, event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()}
                className="clinic-control min-w-0 flex-1 font-mono text-slate-900"
              />
              {entry !== node.id && (
                <button
                  onClick={() => makeEntry(node.id)}
                  className="shrink-0 text-xs font-medium text-slate-500 hover:text-emerald-600"
                >
                  marcar como entrada
                </button>
              )}
            </div>

            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Qué hace en este paso
              </span>
              <textarea
                value={node.data.prompt}
                onChange={(event) => patchNode(node.id, { prompt: event.target.value })}
                rows={8}
                className="clinic-control mt-1 w-full"
              />
            </label>

            <details className="group rounded-xl border border-slate-200">
              <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2.5 text-sm font-medium text-slate-600 [&::-webkit-details-marker]:hidden">
                Ajustes avanzados
                <svg
                  viewBox="0 0 24 24"
                  className="h-4 w-4 shrink-0 fill-none stroke-current text-slate-400 transition-transform group-open:rotate-180"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </summary>
              <div className="space-y-4 border-t border-slate-200 px-3 py-3">
                <div>
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Acciones permitidas
                  </span>
                  <div className="mt-1 space-y-1">
                    {tools.map((tool) => (
                      <label key={tool.name} className="flex items-start gap-2 text-sm text-slate-700">
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
                          className="mt-1 accent-emerald-600"
                        />
                        <span>
                          <span className="text-slate-700">{toolLabel(tool.name)}</span>
                          <span className="block text-[11px] text-slate-400">{tool.description}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>

                <label className="block">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
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
            </details>
          </div>
        )}

        {edge && (
          <div className="space-y-4">
            <h2 className="font-medium text-slate-900">
              {edge.source} <span className="text-slate-400">→</span> {edge.target}
            </h2>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Datos necesarios para continuar
              </span>
              <KeyList
                value={edge.data?.requires ?? []}
                draft={draftKeys}
                setDraft={setDraftKeys}
                onCommit={(keys) => patchEdge(edge.id, keys)}
                placeholder="p. ej. patient_id"
              />
              <span className="mt-1 block text-[11px] text-slate-400">
                El agente no puede pasar a {edge.target} hasta que haya registrado estos datos.
              </span>
            </label>
          </div>
        )}
      </aside>
    </div>
  );
}
