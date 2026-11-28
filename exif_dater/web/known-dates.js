// The known dates that models are tested against: show, confirm, fix, add and remove them.

import { whenJobEnds } from "./jobs.js";
import { attempt, byId, element, report, saveQueue } from "./page.js";
import { call, refresh, state, url } from "./server.js";

// One queue for every card, because they all change the same file.
const queue = saveQueue();

function within() {
  return Number(byId("within").value);
}

// Says where the known dates come from, or why they can't be used.
export function showTruthSource() {
  const { count, source, error } = state.truth;
  byId("truth").textContent = error || `${count} photos with known dates from ${source}`;
}

async function showSummary(known) {
  const confirmed = known.entries.filter((entry) => entry.confirmed).length;
  const doubtful = known.entries.filter((entry) => !entry.confirmed && entry.doubts.length).length;
  byId("known-summary").textContent = known.entries.length
    ? `(${confirmed} of ${known.entries.length} confirmed, ${doubtful} to look at)`
    : "(none yet)";
  const options = known.unlisted.map((file) => element("option", { value: file }, file));
  byId("known-file").replaceChildren(...options);
  byId("known-add").disabled = !known.unlisted.length;
  await refresh();
  showTruthSource();
}

function photo(entry) {
  const figure = element("figure", {});
  if (entry.present) {
    const image = element("img", { src: url("/thumbnail", { file: entry.file }), alt: entry.file,
      loading: "lazy" });
    // The larger copy makes printed date stamps readable.
    const large = url("/thumbnail", { file: entry.file, large: 1 });
    figure.append(element("a", { href: large, target: "_blank" }, image));
  }
  figure.append(element("figcaption", {}, entry.file));
  return figure;
}

function card(entry) {
  const date = element("input", {
    value: entry.date, placeholder: "YYYY-MM-DD", size: 12, ariaLabel: `Date of ${entry.file}`,
  });
  const confirmed = element("input", { type: "checkbox", checked: entry.confirmed });
  const remove = element("button", { textContent: "Remove" });
  const doubts = element("ul", { className: "doubts" });
  // The doubts come last, so that the controls don't move when the list changes.
  const section = element("section", { className: "card" }, photo(entry),
    element("div", { className: "controls" },
      date, element("label", {}, confirmed, " Confirmed"), remove),
    doubts);
  date.disabled = confirmed.disabled = !entry.present;

  let saved = entry;
  const show = () => {
    date.value = saved.date;
    confirmed.checked = saved.confirmed;
    section.classList.toggle("approved", saved.confirmed);
    doubts.replaceChildren(...saved.doubts.map((doubt) => element("li", {}, doubt)));
  };
  show();

  const save = (isConfirmed) => {
    const decision = { file: entry.file, date: date.value, confirmed: isConfirmed };
    queue(async (isLatest) => {
      let known;
      try {
        known = await call("/api/truth", { ...decision, within: within() });
      } catch (error) {
        confirmed.checked = saved.confirmed;
        throw error;
      }
      report("");
      saved = known.entries.find((other) => other.file === entry.file);
      if (isLatest()) show();
      await showSummary(known);
    });
  };
  // A changed date has to be confirmed again.
  date.addEventListener("change", () => {
    confirmed.checked = false;
    save(false);
  });
  confirmed.addEventListener("change", () => save(confirmed.checked));
  remove.addEventListener("click", () => queue(async () => {
    const known = await call("/api/truth/remove", { file: entry.file, within: within() });
    section.remove();
    report("");
    await showSummary(known);
  }));
  return section;
}

// Known dates to look at first: doubtful ones, then unconfirmed ones.
function attention(entry) {
  if (entry.confirmed) return 2;
  return entry.doubts.length ? 0 : 1;
}

async function showAll(known) {
  const sorted = [...known.entries].sort(
    (first, second) => attention(first) - attention(second) || first.file.localeCompare(second.file));
  byId("known-entries").replaceChildren(...sorted.map(card));
  await showSummary(known);
}

async function load() {
  // First, so that a truth.csv that can't be read is explained even though the list then fails.
  await refresh();
  showTruthSource();
  await showAll(await call("/api/truth", undefined, { within: within() }));
}

async function add() {
  const known = await call("/api/truth", {
    file: byId("known-file").value, date: byId("known-date").value, confirmed: false, within: within(),
  });
  byId("known-date").value = "";
  report("");
  await showAll(known);
}

// A model test gives new answers to compare the known dates with.
whenJobEnds(async (job) => {
  if (job?.kind === "evaluate") await load();
});

byId("known-add").addEventListener("click", () => attempt(add));
byId("within").addEventListener("change", () => attempt(load));
byId("show-quality").addEventListener("click", () => attempt(load));
