import assert from "node:assert/strict";
import test from "node:test";
import { getReturnPoints, getReturnRoute, RETURN_PORTS } from "./builderRouting.js";

const demoNodes = [
  { id: "identificar", position: { x: 0, y: 200 }, measured: { width: 256, height: 103 } },
  { id: "registrar", position: { x: 330, y: 40 }, measured: { width: 242, height: 79 } },
  { id: "atender", position: { x: 330, y: 200 }, measured: { width: 256, height: 128 } },
  { id: "reservar", position: { x: 680, y: 120 }, measured: { width: 256, height: 103 } },
  { id: "modificar", position: { x: 680, y: 300 }, measured: { width: 256, height: 103 } },
];

// Label bounds measured in the live builder, including a small clearance for the stroke.
const conditionLabels = [
  { id: "caller_unknown", x: 247, y: 152, width: 92, height: 26 },
  { id: "patient_id", x: 260, y: 244, width: 66, height: 26 },
  { id: "slot_elegido", x: 595, y: 204, width: 76, height: 26 },
  { id: "appointment_id", x: 586, y: 294, width: 94, height: 26 },
];

const returnPairs = [
  ["reservar", "atender"],
  ["modificar", "atender"],
  ["atender", "identificar"],
  ["registrar", "identificar"],
];

function routePoints(nodes, [from, to]) {
  const source = nodes.find((node) => node.id === from);
  const target = nodes.find((node) => node.id === to);
  const route = getReturnRoute(source, target, nodes);
  const port = (node, fraction) => ({
    x: node.position.x + node.measured.width * fraction,
    y: route.side === "top" ? node.position.y - 3 : node.position.y + node.measured.height + 3,
  });
  return getReturnPoints(port(source, RETURN_PORTS.source), port(target, RETURN_PORTS.target), route);
}

function assertClear(points, obstacles) {
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1];
    const end = points[index];
    for (const box of obstacles) {
      const intersects = start.x === end.x
        ? start.x > box.x && start.x < box.x + box.width &&
          Math.max(start.y, end.y) > box.y && Math.min(start.y, end.y) < box.y + box.height
        : start.y > box.y && start.y < box.y + box.height &&
          Math.max(start.x, end.x) > box.x && Math.min(start.x, end.x) < box.x + box.width;
      assert.equal(intersects, false, `Return route crosses ${box.id}`);
    }
  }
}

const cardBounds = (nodes) => nodes.map((node) => ({
  id: node.id,
  ...node.position,
  ...node.measured,
}));

test("demo return routes clear every condition label and stage card", () => {
  for (const pair of returnPairs) {
    assertClear(routePoints(demoNodes, pair), [...conditionLabels, ...cardBounds(demoNodes)]);
  }
});

test("routes stay outside cards when a stage is dragged or grows taller", () => {
  const moved = demoNodes.map((node) => node.id === "reservar"
    ? { ...node, position: { x: 720, y: 80 } }
    : node.id === "atender"
      ? { ...node, measured: { ...node.measured, height: 176 } }
      : node);
  for (const pair of returnPairs) {
    assertClear(routePoints(moved, pair), cardBounds(moved));
  }
});

for (const [id, y] of [["atender", 190], ["registrar", 0]]) {
  test(`moving ${id} upward keeps both return approaches clear`, () => {
    const moved = demoNodes.map((node) => node.id === id
      ? { ...node, position: { ...node.position, y } }
      : node);
    for (const pair of returnPairs) {
      assertClear(routePoints(moved, pair), cardBounds(moved));
    }
  });
}
