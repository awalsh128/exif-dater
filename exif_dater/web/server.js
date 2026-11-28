// Talking to the app's server, and the last state it sent.

const token = new URLSearchParams(location.search).get("token") ?? "";

export function url(path, parameters = {}) {
  const query = new URLSearchParams({ ...parameters, token });
  return `${path}?${query}`;
}

export async function call(path, body, parameters) {
  const options = body === undefined ? {} : { method: "POST", body: JSON.stringify(body) };
  const response = await fetch(url(path, parameters), options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

// Importers see each new value.
export let state;

// Takes a state that came back with the reply to a change.
export function adopt(next) {
  state = next;
}

export async function refresh() {
  adopt(await call("/api/state"));
}
