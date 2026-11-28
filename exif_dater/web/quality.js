// The model quality test: choose models, run it and show how each did.

import { confirmSending, follow, whenJobShown } from "./jobs.js";
import { showTruthSource } from "./known-dates.js";
import { attempt, byId, dollars, element, report } from "./page.js";
import { call, refresh, state } from "./server.js";

export function showModelChoices() {
  const choices = state.models.map((model) => {
    const box = element("input", {
      type: "checkbox", value: model.id, checked: state.default_comparison.includes(model.id),
    });
    const estimate = model.cost_per_photo === null ? null : model.cost_per_photo * 100;
    return element("label", {}, box, ` ${model.id} (about ${dollars(estimate)} per 100 photos) `);
  });
  byId("comparison").append(...choices);
}

function share(part, whole) {
  return whole ? `${part}/${whole}` : "-";
}

function table(headings, rows) {
  const header = element("tr", {}, ...headings.map((heading) => element("th", {}, heading)));
  return element("table", {}, header, ...rows);
}

function row(cells, properties = {}) {
  return element("tr", properties, ...cells.map((cell) => element("td", {}, cell)));
}

function scoreTable(result) {
  const headings = ["Model", `Within ${result.within_years} y`, "Exact year", "Median error",
    "Month", "Within, by stated confidence", "Failed", "Cost per 100"];
  const rows = result.scores.map((score) => {
    const levels = ["high", "medium", "low"]
      .filter((level) => score.by_confidence[level])
      .map((level) => `${level} ${share(...score.by_confidence[level])}`);
    const error = score.median_year_error === null ? "-" : `${score.median_year_error} y`;
    const cost = score.cost_per_100 === null ? "unknown" : `$${score.cost_per_100.toFixed(3)}`;
    const cells = [score.model, share(score.within, score.photos), share(score.exact_year, score.photos),
      error, share(score.month_correct, score.month_known), levels.join(", ") || "-", String(score.failed),
      cost];
    return row(cells, { className: score.model === result.recommended ? "recommended" : "" });
  });
  return table(headings, rows);
}

function answerTable(result) {
  const headings = ["Photo", "Known", "Model", "Estimate", "Confidence", "Clues"];
  const sorted = [...result.answers].sort((first, second) => first.file.localeCompare(second.file));
  const rows = sorted.map((answer) => row(
    [answer.file, answer.known, answer.model, answer.date || "none", answer.confidence, answer.clues]));
  return element("details", {}, element("summary", {}, "Every answer"),
    element("div", { className: "scroll" }, table(headings, rows)));
}

function showReport(result) {
  const verdict = result.recommended
    ? `Cheapest model meeting ${result.target_percent}%: ${result.recommended}`
    : `No model met ${result.target_percent}%.`;
  const spent = result.spent === null ? "" : ` Spent on this run: ${dollars(result.spent)}.`;
  byId("report").replaceChildren(
    element("h2", {}, verdict),
    element("div", {}, `Known dates: ${result.truth_source}.${spent} Prices as of ${result.prices_as_of}.`),
    ...result.warnings.map((warning) => element("p", { className: "error" }, warning)),
    element("div", { className: "scroll" }, scoreTable(result)),
    answerTable(result));
}

async function runTest() {
  report("");
  const checked = document.querySelectorAll("#comparison input:checked");
  const models = [...checked].map((box) => box.value);
  if (!models.length) throw new Error("Choose at least one model.");
  await refresh();
  showTruthSource();
  if (state.truth.error) throw new Error(state.truth.error);
  const costs = models.map((id) => state.models.find((listed) => listed.id === id).cost_per_photo);
  let total = null;
  if (!costs.includes(null)) {
    const perPhoto = costs.reduce((sum, cost) => sum + cost, 0);
    total = perPhoto * state.truth.count;
  }
  const question = `Send up to ${models.length * state.truth.count} requests to the Anthropic API? ` +
    `Estimated cost, at most: ${dollars(total)}. Answers from earlier tests are reused.`;
  if (!(await confirmSending(question))) return;
  const within = Number(byId("within").value);
  const target = Number(byId("target").value);
  await follow(await call("/api/evaluate", { models, within, target }));
}

whenJobShown((job) => {
  if (job.kind === "evaluate" && job.result) showReport(job.result);
});

byId("evaluate").addEventListener("click", () => attempt(runTest));
