// The review view: a paged gallery of proposed dates to analyze, approve and write.

import { savedGroup, saveDecision, sortedGroups, whenDecided } from "./groups.js";
import { confirmSending, follow, setBusy, whenJobEnds } from "./jobs.js";
import { arrowStep, attempt, byId, dollars, element, report } from "./page.js";
import { adopt, call, refresh, state, url } from "./server.js";
import { openViewer, whenViewerCloses } from "./viewer.js";

export function showModelChoice() {
  const options = state.models.map((model) =>
    element("option", { value: model.id, selected: model.id === state.default_model }, model.id));
  byId("model").replaceChildren(...options);
}

function showSummary() {
  const approved = state.groups.filter((group) => group.approved).length;
  byId("progress").textContent = `${approved} of ${state.groups.length} groups approved`;
  byId("new-photos").textContent = `${state.new_photos} new photos, ${state.photos_to_send} need the model`;
}

function tilePhoto(group, file) {
  const image = element("img", { src: url("/thumbnail", { file }), alt: file, loading: "lazy" });
  const link = element("a", { href: url("/thumbnail", { file, large: 1 }), target: "_blank",
    title: `View ${file}` }, image);
  // A plain click opens the viewer. Modified clicks still open the photo in another tab.
  link.addEventListener("click", (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    openViewer(file);
  });
  const figure = element("figure", {}, link);
  if (group.files.length === 1) return figure;

  const label = `Move ${file} to its own group`;
  const split = element("button", { className: "split", textContent: "Split", title: label, ariaLabel: label });
  split.addEventListener("click", () => attempt(async () => {
    adopt(await call("/api/split", { file }));
    render();
  }));
  figure.append(split);
  return figure;
}

function tile(group) {
  const date = element("input", {
    name: "date", value: group.date, placeholder: "YYYY-MM-DD", size: 12, ariaLabel: "Date",
  });
  const description = element("input", {
    name: "description", value: group.description, placeholder: "Description (optional)", ariaLabel: "Description",
  });
  const approved = element("input", { type: "checkbox", checked: group.approved });
  const written = element("span", { className: "written muted" }, group.written ? "Written" : "");

  const save = () => saveDecision({
    id: group.id, date: date.value.trim(), description: description.value, approved: approved.checked,
  });
  for (const input of [date, description, approved]) input.addEventListener("change", save);

  const photos = group.files.map((file) => tilePhoto(group, file));
  const section = element("section", { className: group.approved ? "card tile approved" : "card tile" },
    element("h2", { title: group.title }, group.title),
    element("div", { className: "evidence", title: group.reason },
      element("span", { className: `badge ${group.confidence}` }, group.confidence), ` ${group.reason}`),
    element("div", { className: "photos" }, ...photos),
    element("div", { className: "controls" },
      date, element("label", {}, approved, " Approve"), written, description));
  section.dataset.id = group.id;
  return section;
}

// The gallery shows as many groups as fit the window, one page at a time.
let page = 0;
let pageSize = 1;

// How many tiles fit the window, and the height that makes their rows fill it.
function fitting() {
  const gallery = byId("groups");
  const content = byId("content");
  const style = getComputedStyle(gallery);
  const pixels = (name) => parseFloat(style.getPropertyValue(name));
  const gap = pixels("column-gap");
  const tileWidth = pixels("--tile-width");
  const shortest = pixels("--tile-min-height");
  const tallest = pixels("--tile-max-height");
  // Measured as if nothing were scrolled.
  const top = gallery.getBoundingClientRect().top + content.scrollTop;
  const room = window.innerHeight - top - parseFloat(getComputedStyle(content).paddingBottom);
  const columns = Math.max(1, Math.floor((gallery.clientWidth + gap) / (tileWidth + gap)));
  const rows = Math.max(1, Math.floor((room + gap) / (shortest + gap)));
  const height = Math.floor((room - (rows - 1) * gap) / rows);
  return { size: columns * rows, height: Math.min(tallest, Math.max(shortest, height)) };
}

function setTileHeight(height) {
  byId("groups").style.setProperty("--tile-height", `${height}px`);
}

function pageCount() {
  return Math.max(1, Math.ceil(state.groups.length / pageSize));
}

export function render() {
  // The rows above the gallery have their full height only once filled in.
  showSummary();
  byId("pager").hidden = !state.groups.length;
  // A hidden gallery has no size to measure.
  if (!byId("review").hidden) {
    const { size, height } = fitting();
    pageSize = size;
    setTileHeight(height);
  }
  page = Math.min(page, pageCount() - 1);
  const first = page * pageSize;
  const shown = sortedGroups().slice(first, first + pageSize);
  const empty = element("p", { className: "empty" },
    "No proposed dates yet. Choose Analyze new photos to start.");
  byId("groups").replaceChildren(...(shown.length ? shown.map(tile) : [empty]));
  byId("page-position").textContent = `Page ${page + 1} of ${pageCount()}`;
  byId("page-previous").disabled = page === 0;
  byId("page-next").disabled = page >= pageCount() - 1;
}

function turnPage(by) {
  const next = page + by;
  if (next < 0 || next >= pageCount()) return;
  // Leaving a field saves what was typed in it.
  document.activeElement?.blur();
  page = next;
  render();
  byId("content").scrollTop = 0;
}

function showPageOf(id) {
  const index = sortedGroups().findIndex((group) => group.id === id);
  page = Math.floor(index / pageSize);
  render();
}

// Keeps the first group on screen when the window changes how many fit.
export function fitGallery() {
  if (!state || byId("review").hidden || byId("groups").contains(document.activeElement)) return;
  const { size, height } = fitting();
  setTileHeight(height);
  if (size === pageSize) return;
  page = Math.floor((page * pageSize) / size);
  render();
}

// A tile is updated where it stands, so that a click already under way still lands.
function showDecision(id, error, isLatest) {
  if (!error) showSummary();
  const section = document.querySelector(`#groups section[data-id="${id}"]`);
  if (!section) return;
  const approved = section.querySelector("input[type=checkbox]");
  if (error) {
    // Nothing was saved, so nothing may look approved. What was typed stays, to be corrected.
    approved.checked = false;
    return;
  }
  if (!isLatest) return;
  const group = savedGroup(id);
  for (const name of ["date", "description"]) {
    const input = section.querySelector(`input[name=${name}]`);
    // Never over a field being typed in.
    if (input !== document.activeElement) input.value = group[name];
  }
  approved.checked = group.approved;
  section.classList.toggle("approved", group.approved);
  section.querySelector(".written").textContent = group.written ? "Written" : "";
}

async function analyzeNewPhotos() {
  report("");
  await refresh();
  showSummary();
  const model = byId("model").value;
  const price = state.models.find((listed) => listed.id === model).cost_per_photo;
  const total = price === null ? null : price * state.photos_to_send;
  const question = `Send ${state.photos_to_send} photos to the Anthropic API using ${model}? ` +
    `Estimated cost: ${dollars(total)}.`;
  if (state.photos_to_send && !(await confirmSending(question))) return;
  await follow(await call("/api/analyze", { model }));
}

async function approveHighConfidence() {
  const waiting = state.groups.filter((group) => group.confidence === "high" && group.date && !group.approved);
  try {
    for (const group of waiting) {
      adopt(await call("/api/review", { ...group, approved: true }));
    }
    report("");
  } finally {
    render();
  }
}

async function writeApproved() {
  setBusy(true);
  report("Writing...");
  try {
    const result = await call("/api/apply", {});
    adopt(result.state);
    render();
    const summary = `Wrote ${result.written} photos to ${result.folder}`;
    report(result.error ? `${summary}\n${result.error}` : summary, Boolean(result.error));
  } finally {
    setBusy(false);
  }
}

whenDecided(showDecision);
whenJobEnds(render);
// Returns the gallery to the page of the group last looked at.
whenViewerCloses(showPageOf);

byId("analyze").addEventListener("click", () => attempt(analyzeNewPhotos));
byId("approve-high").addEventListener("click", () => attempt(approveHighConfidence));
byId("apply").addEventListener("click", () => attempt(writeApproved));
byId("page-previous").addEventListener("click", () => turnPage(-1));
byId("page-next").addEventListener("click", () => turnPage(1));
window.addEventListener("resize", fitGallery);
document.addEventListener("keydown", (event) => {
  // An open panel keeps the keys to itself.
  if (byId("review").hidden || document.querySelector("dialog[open]")) return;
  const by = arrowStep(event);
  if (!by) return;
  event.preventDefault();
  turnPage(by);
});
