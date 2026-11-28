// The groups of photos under review: their order and the decisions made on them.

import { report, saveQueue } from "./page.js";
import { adopt, call, state } from "./server.js";

const CONFIDENCE_ORDER = { low: 0, medium: 1, high: 2 };

// Least certain first, because those need attention.
function byAttention(first, second) {
  const confidence = CONFIDENCE_ORDER[first.confidence] - CONFIDENCE_ORDER[second.confidence];
  return confidence || first.id - second.id;
}

export function sortedGroups() {
  return [...state.groups].sort(byAttention);
}

export function savedGroup(id) {
  return state.groups.find((group) => group.id === id);
}

const decisionListeners = [];

// The listener is told the group, why saving failed ("" if it didn't), and whether a newer save waits.
export function whenDecided(listener) {
  decisionListeners.push(listener);
}

// One queue for each group, so that its saves arrive in the order made.
const queues = new Map();

export function saveDecision(decision) {
  if (!queues.has(decision.id)) queues.set(decision.id, saveQueue());
  const queue = queues.get(decision.id);
  return queue(async (isLatest) => {
    let error = "";
    try {
      adopt(await call("/api/review", decision));
    } catch (failure) {
      error = failure.message;
    }
    report(error, Boolean(error));
    for (const listener of decisionListeners) listener(decision.id, error, isLatest());
  });
}
