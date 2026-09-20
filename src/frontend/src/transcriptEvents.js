export function collapseTranscriptPartials(events, live) {
  let pendingPartial = -1;

  if (live) {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const { kind } = events[index];
      if (kind === "stt_final") break;
      if (kind === "stt_partial") {
        pendingPartial = index;
        break;
      }
    }
  }

  return events.filter(
    (event, index) => event.kind !== "stt_partial" || index === pendingPartial,
  );
}
