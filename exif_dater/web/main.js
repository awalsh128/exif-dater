// Starts the page: switches between its views and loads what each shows first.

import "./help.js";
import { follow } from "./jobs.js";
import { attempt, byId } from "./page.js";
import { showModelChoices } from "./quality.js";
import { fitGallery, render, showModelChoice } from "./review.js";
import { refresh, state } from "./server.js";

const VIEWS = ["review", "quality"];

function showView(view) {
  for (const other of VIEWS) {
    byId(other).hidden = other !== view;
    byId(`show-${other}`).ariaPressed = String(other === view);
  }
  // The window may have changed size while the gallery was out of sight.
  if (view === "review") fitGallery();
}

for (const view of VIEWS) {
  byId(`show-${view}`).addEventListener("click", () => showView(view));
}

attempt(async () => {
  await refresh();
  showModelChoice();
  showModelChoices();
  render();
  // After a reload, returns to a task still running or one that failed. A successful one would only take
  // room from the gallery.
  if (state.job?.running || state.job?.error) await follow(state.job);
});
