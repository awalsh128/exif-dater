// The help panel: what was diagnosed, the fixes it offers and how to copy its commands.

import { follow } from "./jobs.js";
import { byId, element } from "./page.js";
import { call } from "./server.js";

const STATUS_MARKS = { ok: "✓", unknown: "?", warning: "!", problem: "✕" };

// The buttons that fix what a finding reports.
const FIXES = {
  "sign-in": { label: "Sign in with browser", run: signIn },
  "check-credit": { label: "Test credit (costs under $0.01)", run: testCredit },
  retry: { label: "Run the analysis again", run: retryAnalysis },
};

// Like attempt(), but reports inside the help panel, which covers the page's own status line.
async function attemptInPanel(action) {
  try {
    await action();
  } catch (error) {
    byId("help-status").textContent = error.message;
  }
}

// Copies the text and says so on the button.
async function copy(button, text) {
  await navigator.clipboard.writeText(text);
  button.textContent = "Copied";
}

function fixes(finding) {
  const offered = element("div", { className: "fixes" });
  if (finding.action) {
    const { label, run } = FIXES[finding.action];
    const button = element("button", { className: "primary", textContent: label });
    button.addEventListener("click", () => attemptInPanel(() => run(button)));
    offered.append(button);
  }
  if (finding.link) {
    offered.append(element("a", {
      href: finding.link, target: "_blank", rel: "noreferrer", textContent: "Open billing page",
    }));
  }
  if (finding.command) {
    const button = element("button", { textContent: "Copy" });
    button.addEventListener("click", () => attemptInPanel(() => copy(button, finding.command)));
    offered.append(element("span", {}, "Run in a terminal: "), element("code", {}, finding.command), button);
  }
  return offered;
}

function item(finding) {
  const body = element("div", {}, element("strong", {}, finding.title),
    element("div", { className: "detail" }, finding.detail));
  const offered = fixes(finding);
  if (offered.hasChildNodes()) body.append(offered);
  const mark = element("span", { className: "mark", ariaLabel: finding.status }, STATUS_MARKS[finding.status]);
  return element("li", { className: finding.status }, mark, body);
}

function showFindings(findings) {
  byId("findings").replaceChildren(...findings.map(item));
  const problems = findings.filter((finding) => ["problem", "warning"].includes(finding.status)).length;
  byId("help-status").textContent = problems
    ? `${problems} to fix.`
    : "No problems found. Test the credit if requests are still refused.";
  byId("show-help").classList.toggle("attention", problems > 0);
}

async function runDiagnosis() {
  byId("help-status").textContent = "Checking...";
  byId("findings").replaceChildren();
  showFindings(await call("/api/diagnose"));
}

function openHelp() {
  if (!byId("help").open) byId("help").showModal();
  return attemptInPanel(runDiagnosis);
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
byId("help-again").addEventListener("click", () => attemptInPanel(runDiagnosis));
byId("help-close").addEventListener("click", () => byId("help").close());
byId("copy-takeout").addEventListener("click", () => {
  attemptInPanel(() => copy(byId("copy-takeout"), byId("takeout-command").textContent));
});
