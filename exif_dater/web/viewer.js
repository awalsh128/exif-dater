// A closer look at one photo of the review gallery, with the same decisions.

import { savedGroup, saveDecision, sortedGroups, whenDecided } from "./groups.js";
import { arrowStep, attempt, byId } from "./page.js";
import { url } from "./server.js";

// The photo on screen.
let viewed = null;
const closeListeners = [];

// The listener is told the group last looked at.
export function whenViewerCloses(listener) {
  closeListeners.push(listener);
}

// Every photo in the order of the gallery.
function reviewPhotos() {
  return sortedGroups().flatMap((group) => group.files.map((file) => ({ file, id: group.id })));
}

function shows(id) {
  return byId("viewer").open && viewed?.id === id;
}

function showMessage(text, isError = false) {
  byId("viewer-message").textContent = text;
  byId("viewer-message").className = isError ? "error" : "muted";
}

function largePhoto(file) {
  return url("/thumbnail", { file, large: 1 });
}

function showPhoto(photo) {
  viewed = photo;
  const photos = reviewPhotos();
  const index = photos.findIndex((other) => other.file === photo.file);
  const group = savedGroup(photo.id);
  const shared = group.files.length > 1 ? `, date shared by ${group.files.length} photos` : "";
  byId("viewer-title").textContent = photo.file;
  byId("viewer-position").textContent = `Photo ${index + 1} of ${photos.length}${shared}`;
  const image = byId("viewer-image");
  image.src = largePhoto(photo.file);
  image.alt = photo.file;
  byId("viewer-confidence").textContent = `${group.confidence} confidence`;
  byId("viewer-confidence").className = `badge ${group.confidence}`;
  byId("viewer-reason").textContent = group.reason;
  byId("viewer-date").value = group.date;
  byId("viewer-description").value = group.description;
  byId("viewer-approved").checked = group.approved;
  byId("viewer-previous").disabled = index === 0;
  byId("viewer-next").disabled = index === photos.length - 1;
  showMessage("");
  // Fetched ahead, so that the next photo appears at once.
  const following = photos[index + 1];
  if (following) new Image().src = largePhoto(following.file);
}

export function openViewer(file) {
  byId("viewer").showModal();
  showPhoto(reviewPhotos().find((photo) => photo.file === file));
  byId("viewer-next").focus();
}

function step(by) {
  const photos = reviewPhotos();
  const index = photos.findIndex((photo) => photo.file === viewed.file);
  const next = photos[index + by];
  if (next) showPhoto(next);
}

function decisionShown() {
  return {
    id: viewed.id,
    date: byId("viewer-date").value.trim(),
    description: byId("viewer-description").value,
    approved: byId("viewer-approved").checked,
  };
}

// The next group still to approve after this one, going round to the start after the last.
function nextUnapproved(id) {
  const groups = sortedGroups();
  const here = groups.findIndex((group) => group.id === id);
  const others = [...groups.slice(here + 1), ...groups.slice(0, here)];
  return others.find((group) => !group.approved);
}

async function approveAndNext() {
  const { id } = viewed;
  byId("viewer-approved").checked = true;
  await saveDecision(decisionShown());
  if (!shows(id) || !savedGroup(id).approved) return;
  const next = nextUnapproved(id);
  if (next) showPhoto({ file: next.files[0], id: next.id });
  else showMessage("Every group is approved. Close the viewer, then write the approved photos.");
}

function setZoom(actual) {
  byId("viewer-frame").classList.toggle("actual", actual);
  byId("viewer-zoom").ariaPressed = String(actual);
}

function toggleZoom() {
  setZoom(!byId("viewer-frame").classList.contains("actual"));
}

whenDecided((id, error, isLatest) => {
  if (!shows(id)) return;
  if (error) {
    // Nothing was saved, so nothing may look approved. What was typed stays, to be corrected.
    byId("viewer-approved").checked = false;
    showMessage(error, true);
    return;
  }
  if (!isLatest) return;
  const group = savedGroup(id);
  const fill = (input, value) => {
    // Never over a field being typed in.
    if (input !== document.activeElement) input.value = value;
  };
  fill(byId("viewer-date"), group.date);
  fill(byId("viewer-description"), group.description);
  byId("viewer-approved").checked = group.approved;
  showMessage(group.approved ? "Approved." : "");
});

for (const id of ["viewer-date", "viewer-description", "viewer-approved"]) {
  byId(id).addEventListener("change", () => saveDecision(decisionShown()));
}
byId("viewer-approve-next").addEventListener("click", () => attempt(approveAndNext));
byId("viewer-previous").addEventListener("click", () => step(-1));
byId("viewer-next").addEventListener("click", () => step(1));
byId("viewer-zoom").addEventListener("click", toggleZoom);
byId("viewer-image").addEventListener("click", toggleZoom);
byId("viewer-close").addEventListener("click", () => byId("viewer").close());
byId("viewer").addEventListener("keydown", (event) => {
  const by = arrowStep(event);
  if (!by) return;
  event.preventDefault();
  step(by);
});
byId("viewer").addEventListener("close", () => {
  for (const listener of closeListeners) listener(viewed.id);
});
