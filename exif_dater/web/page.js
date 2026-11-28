// Finding and building the page's elements, reporting to the user and running what they ask for.

export const byId = (id) => document.getElementById(id);

export function element(tag, properties = {}, ...children) {
  const node = Object.assign(document.createElement(tag), properties);
  node.append(...children);
  return node;
}

export function report(message, isError = false) {
  const status = byId("status");
  status.textContent = message;
  status.className = isError ? "error" : "";
}

// Runs an action and shows its failure instead of losing it.
export async function attempt(action) {
  try {
    await action();
  } catch (error) {
    report(error.message, true);
  }
}

// Returns a function that runs saves one after another, in the order made. Each is told whether a newer
// one is waiting, so that an older reply never overwrites what the user did since.
export function saveQueue() {
  let last = Promise.resolve();
  let made = 0;
  return (save) => {
    const mine = ++made;
    const isLatest = () => mine === made;
    last = last.then(() => attempt(() => save(isLatest)));
    return last;
  };
}

export function dollars(amount) {
  if (amount === null) return "unknown";
  if (amount < 0.01) return "under $0.01";
  return `$${amount.toFixed(2)}`;
}

const ARROWS = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };

// Which way an arrow key points, or nothing where the key has another job: moving the cursor in a field,
// changing a list's choice, or part of a browser shortcut.
export function arrowStep(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return undefined;
  if (event.target.matches("input:not([type=checkbox]), select, textarea")) return undefined;
  return ARROWS[event.key];
}
