import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const launcher = fileURLToPath(new URL("../bin/exif-dater.js", import.meta.url));
const URL_LINE = "Review at http://127.0.0.1:4321/?token=abc-DEF_1";

// Runs the launcher with stand-ins for uv and the other tools, which record how they were called.
function launch(t, args, { missing = [], exitCode = 0, settings = null } = {}) {
  const directory = mkdtempSync(join(tmpdir(), "launcher-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const record = join(directory, "uv-arguments");
  const opened = join(directory, "opened");
  const scripts = {
    uv: `[ "$1" = --version ] && exit 0\nprintf '%s\\n' "$@" > '${record}'\necho '${URL_LINE}'\nexit ${exitCode}`,
    magick: "exit 0",
    exiftool: "exit 0",
    browser: `printf '%s' "$1" > '${opened}'`,
  };
  for (const [name, body] of Object.entries(scripts)) {
    if (missing.includes(name)) continue;
    writeFileSync(join(directory, name), `#!/bin/sh\n${body}\n`);
    chmodSync(join(directory, name), 0o755);
  }
  const settingsFile = join(directory, "exif-dater", "env");
  if (settings !== null) {
    mkdirSync(join(directory, "exif-dater"));
    writeFileSync(settingsFile, settings);
  }
  const result = spawnSync(process.execPath, [launcher, ...args], {
    encoding: "utf8",
    // XDG_CONFIG_HOME keeps the tests away from the real saved key.
    env: { PATH: directory, BROWSER: join(directory, "browser"), XDG_CONFIG_HOME: directory },
  });
  const forwarded = existsSync(record) ? readFileSync(record, "utf8").trimEnd().split("\n") : null;
  return {
    ...result,
    forwarded: forwarded?.slice(forwarded.indexOf("exif-dater") + 1),
    uvOptions: forwarded?.slice(0, forwarded.indexOf("exif-dater")),
    settingsFile,
    opened,
  };
}

// The browser runs detached, so give it a moment to write.
async function openedUrl(path) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (existsSync(path) && readFileSync(path, "utf8")) return readFileSync(path, "utf8");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return null;
}

test("launcher given a directory opens its web page", { skip: process.platform === "win32" }, async (t) => {
  const result = launch(t, ["photos", "--tz", "-05:00"]);

  assert.equal(result.status, 0);
  assert.deepEqual(result.forwarded, ["review", "photos", "--tz", "-05:00"]);
  assert.equal(await openedUrl(result.opened), "http://127.0.0.1:4321/?token=abc-DEF_1");
});

test("launcher given --no-open prints the address and keeps the flag", { skip: process.platform === "win32" }, (t) => {
  const result = launch(t, ["photos", "--no-open"]);

  assert.deepEqual(result.forwarded, ["review", "photos"]);
  assert.match(result.stdout, /Review at /);
});

test("launcher given a command forwards it unchanged", { skip: process.platform === "win32" }, (t) => {
  const result = launch(t, ["evaluate", "photos", "--model", "claude-haiku-5-5"], { missing: ["exiftool"] });

  assert.equal(result.status, 0);
  assert.deepEqual(result.forwarded, ["evaluate", "photos", "--model", "claude-haiku-5-5"]);
});

test("launcher without a required tool explains how to install it", { skip: process.platform === "win32" }, (t) => {
  const result = launch(t, ["photos"], { missing: ["exiftool"] });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /exiftool not found.*libimage-exiftool-perl/);
});

test("launcher with a saved key file passes it to uv", { skip: process.platform === "win32" }, (t) => {
  const result = launch(t, ["analyze", "photos"], { settings: "ANTHROPIC_API_KEY='test-key'\n" });

  assert.deepEqual(result.uvOptions.slice(-2), ["--env-file", result.settingsFile]);
});

test("launcher without a saved key file names none", { skip: process.platform === "win32" }, (t) => {
  const result = launch(t, ["analyze", "photos"]);

  assert.ok(!result.uvOptions.includes("--env-file"));
});

test("launcher passes on a failing exit status", { skip: process.platform === "win32" }, (t) => {
  const result = launch(t, ["analyze", "photos"], { exitCode: 3 });

  assert.equal(result.status, 3);
});

test("launcher without arguments prints usage and fails", (t) => {
  const result = launch(t, []);

  assert.equal(result.status, 2);
  assert.match(result.stdout, /usage:/);
});
