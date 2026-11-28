#!/usr/bin/env node
// The npx entry point: checks prerequisites, runs the Python app through uv and opens the browser.
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const USAGE = `usage:
  exif-dater <photo dir> [--tz OFFSET] [--notes FILE] [--no-open]   open the web page
  exif-dater analyze <photo dir> [options]                          estimate dates in the terminal
  exif-dater evaluate <photo dir> [options]                         test models in the terminal
  exif-dater <command> --help                                       list a command's options`;

const COMMANDS = ["analyze", "review", "evaluate"];

const TOOLS = [
  { name: "uv", check: "--version", install: "see https://docs.astral.sh/uv/", commands: COMMANDS },
  { name: "magick", check: "-version", install: "sudo apt install imagemagick", commands: COMMANDS },
  { name: "exiftool", check: "-ver", install: "sudo apt install libimage-exiftool-perl", commands: ["review"] },
];

const REVIEW_URL = /Review at (http:\/\/127\.0\.0\.1:\d+\/\?token=[\w-]+)\r?\n/;

function openBrowser(url) {
  const openers = { darwin: ["open"], win32: ["cmd", "/c", "start", ""] };
  const [program, ...first] = process.env.BROWSER
    ? [process.env.BROWSER]
    : (openers[process.platform] ?? ["xdg-open"]);
  const opener = spawn(program, [...first, url], { stdio: "ignore", detached: true });
  // The address is already printed, so a machine without a browser opener loses nothing.
  opener.on("error", () => {});
  opener.unref();
}

function main(given) {
  if (given.length === 0 || given[0] === "--help" || given[0] === "-h") {
    console.log(USAGE);
    return given.length === 0 ? 2 : 0;
  }
  const open = !given.includes("--no-open");
  const rest = given.filter((argument) => argument !== "--no-open");
  const args = COMMANDS.includes(rest[0]) ? rest : ["review", ...rest];
  const command = args[0];

  for (const tool of TOOLS.filter((needed) => needed.commands.includes(command))) {
    if (spawnSync(tool.name, [tool.check], { stdio: "ignore" }).error) {
      console.error(`error: ${tool.name} not found, install it: ${tool.install}`);
      return 1;
    }
  }

  const project = fileURLToPath(new URL("..", import.meta.url));
  const watchForUrl = command === "review" && open;
  const app = spawn(
    "uv",
    ["run", "--quiet", "--frozen", "--no-dev", "--project", project, "exif-dater", ...args],
    {
      stdio: ["inherit", watchForUrl ? "pipe" : "inherit", "inherit"],
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    },
  );

  if (watchForUrl) {
    let output = "";
    let opened = false;
    app.stdout.on("data", (chunk) => {
      process.stdout.write(chunk);
      if (opened) return;
      output += chunk;
      const match = REVIEW_URL.exec(output);
      if (match) {
        opened = true;
        openBrowser(match[1]);
      }
    });
  }

  // Ctrl+C reaches the app too. Wait for it to finish saving instead of exiting first.
  process.on("SIGINT", () => {});
  process.on("SIGTERM", () => app.kill("SIGTERM"));
  app.on("exit", (code) => process.exit(code ?? 130));
  return undefined;
}

const status = main(process.argv.slice(2));
if (status !== undefined) process.exitCode = status;
