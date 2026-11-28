// Long tasks: asking before anything is sent, following progress and stopping.

import { attempt, byId } from "./page.js";
import { call, refresh } from "./server.js";

const POLL_MILLISECONDS = 1000;
const JOB_NAMES = { analyze: "Analysis", evaluate: "Model test", "sign-in": "Sign-in" };
// These start or depend on a task, so only one task runs at a time.
const BUSY_BUTTONS = ["analyze", "evaluate", "apply", "approve-high"];

const shownListeners = [];
const endListeners = [];

export function whenJobShown(listener) {
  shownListeners.push(listener);
}

export function whenJobEnds(listener) {
  endListeners.push(listener);
}

// Nothing is sent to the API until the user presses "Send" here.
export function confirmSending(question) {
  byId("confirm-question").textContent = question;
  byId("confirm").hidden = false;
  return new Promise((resolve) => {
    const answer = (yes) => {
      byId("confirm").hidden = true;
      resolve(yes);
    };
    byId("confirm-yes").onclick = () => answer(true);
    byId("confirm-no").onclick = () => answer(false);
  });
}

export function setBusy(busy) {
  for (const id of BUSY_BUTTONS) byId(id).disabled = busy;
}

function outcome(job) {
  if (job.running) return "running";
  if (job.error) return "stopped";
  return "finished";
}

function showJob(job) {
  byId("job").hidden = !job;
  if (!job) return;
  byId("job-title").textContent = `${JOB_NAMES[job.kind]} ${outcome(job)}`;
  byId("job-cancel").hidden = !job.running;
  byId("job-close").hidden = job.running;
  byId("job-diagnose").hidden = !job.error;
  byId("job").classList.toggle("failed", Boolean(job.error));
  byId("show-help").classList.toggle("attention", Boolean(job.error));
  const lines = [...job.lines, job.error].filter(Boolean);
  byId("job-log").textContent = lines.join("\n");
  byId("job-log").scrollTop = byId("job-log").scrollHeight;
  for (const listener of shownListeners) listener(job);
}

// Polls until the background task ends, then reloads what it changed.
export async function follow(job) {
  setBusy(true);
  while (job?.running) {
    showJob(job);
    await new Promise((resolve) => setTimeout(resolve, POLL_MILLISECONDS));
    job = await call("/api/job");
  }
  await refresh();
  showJob(job);
  setBusy(false);
  for (const listener of endListeners) await listener(job);
}

byId("job-cancel").addEventListener("click", () => attempt(() => call("/api/cancel", {})));
byId("job-close").addEventListener("click", () => {
  byId("job").hidden = true;
});
