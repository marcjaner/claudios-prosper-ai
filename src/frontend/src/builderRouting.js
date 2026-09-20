export const RETURN_PORTS = { source: 0.35, target: 0.65 };
const RETURN_CLEARANCE = 36;
const CORNER_RADIUS = 12;

function nodeBounds(node) {
  const width = node.measured?.width ?? 256;
  const height = node.measured?.height ?? 104;
  return {
    id: node.id,
    left: node.position.x,
    right: node.position.x + width,
    top: node.position.y,
    bottom: node.position.y + height,
    width,
  };
}

function nodesOnRay(bounds, point, side) {
  return bounds.filter((node) =>
    node.left < point.x && node.right > point.x &&
    (side === "top" ? node.top < point.y : node.bottom > point.y),
  );
}

export function getReturnRoute(sourceNode, targetNode, nodes) {
  const source = nodeBounds(sourceNode);
  const target = nodeBounds(targetNode);
  const bounds = nodes.map(nodeBounds);
  const sourceX = source.left + source.width * RETURN_PORTS.source;
  const targetX = target.left + target.width * RETURN_PORTS.target;
  let side = source.top < target.top ? "top" : "bottom";
  const oppositeSide = side === "top" ? "bottom" : "top";
  const sourceBlockers = nodesOnRay(bounds, { x: sourceX, y: source[side] }, side);
  const oppositeBlockers = nodesOnRay(bounds, { x: sourceX, y: source[oppositeSide] }, oppositeSide);
  if (oppositeBlockers.length < sourceBlockers.length) side = oppositeSide;
  const above = side === "top";
  const direction = above ? -1 : 1;
  const outerY = above ? Math.min(source.top, target.top) : Math.max(source.bottom, target.bottom);
  const blockers = nodesOnRay(bounds, { x: targetX, y: target[side] }, side);

  // A card above/below the destination needs a short detour through its side gutter.
  let detour;
  if (blockers.length) {
    const nearestY = above
      ? Math.max(...blockers.map((node) => node.bottom))
      : Math.min(...blockers.map((node) => node.top));
    const approachGap = Math.min(RETURN_CLEARANCE, Math.abs(target[side] - nearestY) / 2);
    detour = {
      x: Math.max(target.right, ...blockers.map((node) => node.right)) + RETURN_CLEARANCE,
      y: target[side] + direction * approachGap,
    };
  }

  const laneEndX = detour?.x ?? targetX;
  const laneNodes = bounds.filter((node) =>
    node.right > Math.min(sourceX, laneEndX) && node.left < Math.max(sourceX, laneEndX),
  );
  const laneY = above
    ? Math.min(outerY, ...laneNodes.map((node) => node.top)) - RETURN_CLEARANCE
    : Math.max(outerY, ...laneNodes.map((node) => node.bottom)) + RETURN_CLEARANCE;

  return { side, laneY, detour };
}

export function getReturnPoints(source, target, route) {
  return [
    source,
    { x: source.x, y: route.laneY },
    { x: route.detour?.x ?? target.x, y: route.laneY },
    ...(route.detour ? [route.detour, { x: target.x, y: route.detour.y }] : []),
    target,
  ];
}

export function roundedPath(points) {
  let path = `M ${points[0].x},${points[0].y}`;
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = points[index - 1];
    const point = points[index];
    const next = points[index + 1];
    const incoming = Math.hypot(point.x - previous.x, point.y - previous.y);
    const outgoing = Math.hypot(next.x - point.x, next.y - point.y);
    if (!incoming || !outgoing) continue;
    const radius = Math.min(CORNER_RADIUS, incoming / 2, outgoing / 2);
    const before = {
      x: point.x + (previous.x - point.x) * radius / incoming,
      y: point.y + (previous.y - point.y) * radius / incoming,
    };
    const after = {
      x: point.x + (next.x - point.x) * radius / outgoing,
      y: point.y + (next.y - point.y) * radius / outgoing,
    };
    path += ` L ${before.x},${before.y} Q ${point.x},${point.y} ${after.x},${after.y}`;
  }
  const end = points.at(-1);
  return `${path} L ${end.x},${end.y}`;
}
