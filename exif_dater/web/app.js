"use strict";

const token = new URLSearchParams(location.search).get("token") ?? "";
const confidenceOrder = { low: 0, medium: 1, high: 2 };
const POLL_MILLISECONDS = 1000;
const JOB_NAMES = { analyze: "Analysis", evaluate: "Model test", "sign-in": "Sign-in" };
const STATUS_MARKS = { ok: "✓", unknown: "?", warning: "!", problem: "✕" };
let state;

const byId = (id) => document.getElementById(id);

function url(path, parameters = {}) {
  return path + "?" + new URLSearchParams({ ...parameters, token });
}

async function call(path, body) {
  const options = body === undefined ? {} : { method: "POST", body: JSON.stringify(body) };
  const response = await fetch(url(path), options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function report(message, isError = false) {
  const status = byId("status");
  status.textContent = message;
  status.className = isError ? "error" : "";
}

// Runs an action and shows its failure instead of losing it.
async function attempt(action) {
  try {
    await action();
  } catch (error) {
    report(error.message, true);
  }
}

function element(tag, properties = {}, ...children) {
  const node = Object.assign(document.createElement(tag), properties);
  node.append(...children);
  return node;
}

function dollars(amount) {
  if (amount === null) return "unknown";
  return amount < 0.01 ? "under $0.01" : `$${amount.toFixed(2)}`;
}

// Nothing is sent to the API until the user presses "Send" here.
function confirmSending(question) {
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

function card(group) {
  const date = element("input", { value: group.date, placeholder: "YYYY-MM-DD", size: 12, ariaLabel: "Date" });
  const description = element("input", {
    name: "description", value: group.description, placeholder: "Description (optional)", ariaLabel: "Description",
  });
  const approved = element("input", { type: "checkbox", checked: group.approved });

  const save = async () => {
    try {
      state = await call("/api/review", {
        id: group.id, date: date.value.trim(), description: description.value, approved: approved.checked,
      });
      report("");
      // Replace only this card, so unsaved typing and the scroll position stay put.
      section.replaceWith(card(state.groups.find((other) => other.id === group.id)));
      showSummary();
    } catch (error) {
      approved.checked = false;
      report(error.message, true);
    }
  };
  for (const input of [date, description, approved]) input.addEventListener("change", save);

  const photos = group.files.map((file) => {
    const image = element("img", { src: url("/thumbnail", { file }), alt: file, loading: "lazy" });
    const link = element("a", { href: image.src, target: "_blank" }, image);
    const figure = element("figure", {}, link, element("figcaption", {}, file));
    if (group.files.length > 1) {
      const split = element("button", { textContent: "Move to own group" });
      split.addEventListener("click", () => attempt(async () => {
        state = await call("/api/split", { file });
        render();
      }));
      figure.append(split);
    }
    return figure;
  });

  const section = element("section", { className: group.approved ? "card approved" : "card" },
    element("h2", {}, group.title),
    element("div", { className: "evidence" },
      element("span", { className: `badge ${group.confidence}` }, `${group.confidence} confidence`),
      ` ${group.reason}`),
    element("div", { className: "photos" }, ...photos),
    element("div", { className: "controls" },
      date, description, element("label", {}, approved, " Approve"),
      element("span", { className: "muted" }, group.written ? "Written" : "")));
  return section;
}

function showSummary() {
  const approved = state.groups.filter((group) => group.approved).length;
  byId("progress").textContent = `${approved} of ${state.groups.length} groups approved`;
  byId("new-photos").textContent = `${state.new_photos} new photos, ${state.photos_to_send} need the model`;
  const { count, source, error } = state.truth;
  byId("truth").textContent = error || `${count} photos with known dates from ${source}`;
}

function render() {
  // Least certain first, because those need attention.
  const sorted = [...state.groups].sort((a, b) =>
    confidenceOrder[a.confidence] - confidenceOrder[b.confidence] || a.id - b.id);
  const empty = element("p", { className: "empty" },
    "No proposed dates yet. Choose Analyze new photos to start.");
  byId("groups").replaceChildren(...(sorted.length ? sorted.map(card) : [empty]));
  showSummary();
}

function showModels() {
  byId("model").replaceChildren(...state.models.map((model) =>
    element("option", { value: model.id, selected: model.id === state.default_model }, model.id)));
  byId("comparison").append(...state.models.map((model) => {
    const box = element("input", {
      type: "checkbox", value: model.id, checked: state.default_comparison.includes(model.id),
    });
    const estimate = model.cost_per_photo === null ? null : model.cost_per_photo * 100;
    return element("label", {}, box, ` ${model.id} (about ${dollars(estimate)} per 100 photos) `);
  }));
}

function scoreTable(result) {
  const share = (part, whole) => (whole ? `${part}/${whole}` : "-");
  const headings = ["Model", `Within ${result.within_years} y`, "Exact year", "Median error",
    "Month", "Within, by stated confidence", "Failed", "Cost per 100"];
  const rows = result.scores.map((score) => {
    const levels = ["high", "medium", "low"]
      .filter((level) => score.by_confidence[level])
      .map((level) => `${level} ${share(...score.by_confidence[level])}`);
    const cells = [score.model, share(score.within, score.photos), share(score.exact_year, score.photos),
      score.median_year_error === null ? "-" : `${score.median_year_error} y`,
      share(score.month_correct, score.month_known), levels.join(", ") || "-", String(score.failed),
      score.cost_per_100 === null ? "unknown" : `$${score.cost_per_100.toFixed(3)}`];
    return element("tr", { className: score.model === result.recommended ? "recommended" : "" },
      ...cells.map((cell) => element("td", {}, cell)));
  });
  return element("table", {},
    element("tr", {}, ...headings.map((heading) => element("th", {}, heading))), ...rows);
}

function answerTable(result) {
  const headings = ["Photo", "Known", "Model", "Estimate", "Confidence", "Clues"];
  const sorted = [...result.answers].sort((a, b) => a.file.localeCompare(b.file));
  const rows = sorted.map((answer) => {
    const cells = [answer.file, answer.known, answer.model, answer.date || "none",
      answer.confidence, answer.clues];
    return element("tr", {}, ...cells.map((cell) => element("td", {}, cell)));
  });
  return element("details", {}, element("summary", {}, "Every answer"),
    element("div", { className: "scroll" }, element("table", {},
      element("tr", {}, ...headings.map((heading) => element("th", {}, heading))), ...rows)));
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

function showJob(job) {
  byId("job").hidden = !job;
  if (!job) return;
  const name = JOB_NAMES[job.kind];
  const outcome = job.running ? "running" : job.error ? "stopped" : "finished";
  byId("job-title").textContent = `${name} ${outcome}`;
  byId("job-cancel").hidden = !job.running;
  byId("job-close").hidden = job.running;
  byId("job-diagnose").hidden = !job.error;
  byId("job").classList.toggle("failed", Boolean(job.error));
  byId("show-help").classList.toggle("attention", Boolean(job.error));
  byId("job-log").textContent = [...job.lines, job.error].filter(Boolean).join("\n");
  byId("job-log").scrollTop = byId("job-log").scrollHeight;
  if (job.kind === "evaluate" && job.result) showReport(job.result);
}

function setBusy(busy) {
  for (const id of ["analyze", "evaluate", "apply", "approve-high"]) byId(id).disabled = busy;
}

// Polls until the background task ends, then reloads what it changed.
async function follow(job) {
  setBusy(true);
  while (job?.running) {
    showJob(job);
    await new Promise((resolve) => setTimeout(resolve, POLL_MILLISECONDS));
    job = await call("/api/job");
  }
  state = await call("/api/state");
  render();
  showJob(job);
  setBusy(false);
}

byId("analyze").addEventListener("click", () => attempt(async () => {
  report("");
  state = await call("/api/state");
  showSummary();
  const model = byId("model").value;
  const price = state.models.find((listed) => listed.id === model).cost_per_photo;
  const total = price === null ? null : price * state.photos_to_send;
  const question = `Send ${state.photos_to_send} photos to the Anthropic API using ${model}? ` +
    `Estimated cost: ${dollars(total)}.`;
  if (state.photos_to_send && !(await confirmSending(question))) return;
  await follow(await call("/api/analyze", { model }));
}));

byId("evaluate").addEventListener("click", () => attempt(async () => {
  report("");
  const models = [...document.querySelectorAll("#comparison input:checked")].map((box) => box.value);
  if (!models.length) throw new Error("Choose at least one model.");
  state = await call("/api/state");
  showSummary();
  if (state.truth.error) throw new Error(state.truth.error);
  const costs = models.map((id) => state.models.find((listed) => listed.id === id).cost_per_photo);
  const total = costs.includes(null) ? null : costs.reduce((sum, cost) => sum + cost, 0) * state.truth.count;
  const question = `Send up to ${models.length * state.truth.count} requests to the Anthropic API? ` +
    `Estimated cost, at most: ${dollars(total)}. Answers from earlier tests are reused.`;
  if (!(await confirmSending(question))) return;
  await follow(await call("/api/evaluate", {
    models, within: Number(byId("within").value), target: Number(byId("target").value),
  }));
}));

byId("job-cancel").addEventListener("click", () => attempt(() => call("/api/cancel", {})));

byId("approve-high").addEventListener("click", () => attempt(async () => {
  try {
    for (const group of state.groups.filter((g) => g.confidence === "high" && g.date && !g.approved)) {
      state = await call("/api/review", { ...group, approved: true });
    }
    report("");
  } finally {
    render();
  }
}));

byId("apply").addEventListener("click", () => attempt(async () => {
  setBusy(true);
  report("Writing...");
  try {
    const result = await call("/api/apply", {});
    state = result.state;
    render();
    const summary = `Wrote ${result.written} photos to ${result.folder}`;
    report(result.error ? `${summary}\n${result.error}` : summary, Boolean(result.error));
  } finally {
    setBusy(false);
  }
}));

// The buttons that fix what a finding reports.
const FIXES = {
  "sign-in": { label: "Sign in with browser", run: signIn },
  "check-credit": { label: "Test credit (costs under $0.01)", run: testCredit },
  retry: { label: "Run the analysis again", run: retryAnalysis },
};

function showFindings(findings) {
  byId("findings").replaceChildren(...findings.map((finding) => {
    const body = element("div", {}, element("strong", {}, finding.title),
      element("div", { className: "detail" }, finding.detail));
    const fixes = element("div", { className: "fixes" });
    if (finding.action) {
      const { label, run } = FIXES[finding.action];
      const button = element("button", { className: "primary", textContent: label });
      button.addEventListener("click", () => helpAttempt(() => run(button)));
      fixes.append(button);
    }
    if (finding.link) {
      fixes.append(element("a", {
        href: finding.link, target: "_blank", rel: "noreferrer", textContent: "Open billing page",
      }));
    }
    if (finding.command) {
      const copy = element("button", { textContent: "Copy" });
      copy.addEventListener("click", () => helpAttempt(async () => {
        await navigator.clipboard.writeText(finding.command);
        copy.textContent = "Copied";
      }));
      fixes.append(element("span", {}, "Run in a terminal: "), element("code", {}, finding.command), copy);
    }
    if (fixes.hasChildNodes()) body.append(fixes);
    const mark = element("span", { className: "mark", ariaLabel: finding.status }, STATUS_MARKS[finding.status]);
    return element("li", { className: finding.status }, mark, body);
  }));
  const problems = findings.filter((finding) => ["problem", "warning"].includes(finding.status)).length;
  byId("help-status").textContent = problems
    ? `${problems} to fix.`
    : "No problems found. Test the credit if requests are still refused.";
  byId("show-help").classList.toggle("attention", problems > 0);
}

// Like attempt(), but reports inside the help panel, which covers the page's own status line.
async function helpAttempt(action) {
  try {
    await action();
  } catch (error) {
    byId("help-status").textContent = error.message;
  }
}

async function runDiagnosis() {
  byId("help-status").textContent = "Checking...";
  byId("findings").replaceChildren();
  showFindings(await call("/api/diagnose"));
}

function openHelp() {
  if (!byId("help").open) byId("help").showModal();
  return helpAttempt(runDiagnosis);
}

async function signIn() {
  const job = await call("/api/sign-in", {});
  byId("help").close();
  await follow(job);
  await openHelp();
}

async function testCredit(button) {
  button.disabled = true;
  byId("help-status").textContent = "Testing...";
  try {
    showFindings(await call("/api/check-credit", {}));
  } finally {
    button.disabled = false;
  }
}

function retryAnalysis() {
  byId("help").close();
  byId("show-review").click();
  byId("analyze").click();
}

byId("show-help").addEventListener("click", openHelp);
byId("job-diagnose").addEventListener("click", openHelp);
byId("help-again").addEventListener("click", () => helpAttempt(runDiagnosis));
byId("help-close").addEventListener("click", () => byId("help").close());
byId("job-close").addEventListener("click", () => { byId("job").hidden = true; });

for (const view of ["review", "quality"]) {
  byId(`show-${view}`).addEventListener("click", () => {
    for (const other of ["review", "quality"]) {
      byId(other).hidden = other !== view;
      byId(`show-${other}`).ariaPressed = String(other === view);
    }
  });
}

attempt(async () => {
  state = await call("/api/state");
  showModels();
  render();
  // Reconnects to a task that was running before the page was reloaded.
  if (state.job) await follow(state.job);
});
