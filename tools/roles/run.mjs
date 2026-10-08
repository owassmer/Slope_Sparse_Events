#!/usr/bin/env node
// node tools/roles/run.mjs --inbox <file> --prompt <text> -- pi --mode rpc <pi options...>
//
// Runs one worker or verifier the way `pi --mode json "<prompt>"` did (it prompts once, streams Pi's events to
// stdout, and exits when Pi has no more work), but over Pi's RPC mode (Pi's docs/rpc.md), so the coordinator can
// steer it while it runs. scripts/work and scripts/verify call this; scripts/steer writes to the inbox.
//
// stdout carries exactly what JSON mode carried: a session header, then Pi's session events (docs/json.md). RPC's
// command responses and extension UI requests are not passed on; a dialog an extension opens is cancelled, since no
// one is there to answer it. Notes for the run's log go to stderr as "=== " lines.
//
// The inbox is a JSONL file the coordinator appends to, one {"id","kind","message"} per line:
//   kind "steer": Pi's `steer`, delivered after the current tool calls and before the next model call.
//   kind "stop":  Pi's `abort`; the run ends with exit 7 and its work is not applied.
// Each is acknowledged in the log as "=== steered (<id>): ..." or "=== stopped (<id>)", which scripts/steer waits for. A
// steer asks the worker to say, in its next message, what it will change because of it.
// The run ends when Pi reports `agent_settled` (no more automatic work): stdin closes and Pi exits with its own status.
import { spawn } from "node:child_process";
import { closeSync, existsSync, openSync, readSync, statSync, watch, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const argv = process.argv.slice(2);
const sep = argv.indexOf("--");
const opt = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 && i < sep ? argv[i + 1] : undefined;
};
const inbox = opt("--inbox");
const prompt = opt("--prompt");
const command = argv.slice(sep + 1);
if (sep < 0 || !inbox || prompt === undefined || command.length === 0) {
  console.error("usage: run.mjs --inbox <file> --prompt <text> -- pi --mode rpc ...");
  process.exit(2);
}
const note = (line) => process.stderr.write(`=== ${line}\n`);
const one = (s, n = 200) => String(s ?? "").replace(/\s+/g, " ").trim().slice(0, n);

writeFileSync(inbox, "", { flag: "a" });
const pi = spawn(command[0], command.slice(1), { stdio: ["pipe", "pipe", "inherit"] });
const send = (record) => {
  if (!pi.stdin.destroyed) pi.stdin.write(JSON.stringify(record) + "\n");
};

let started = false; // Pi accepted the prompt; the inbox is read from then on (earlier entries wait)
let running = false; // between the prompt starting a run and agent_settled
let stopped = false;
let ending = false;
const pending = new Map(); // command id -> inbox entry awaiting Pi's response

function end() {
  if (ending) return;
  ending = true;
  clearInterval(poll);
  watcher?.close();
  pi.stdin.end();
}

// ---- Pi's stdout: split on LF bytes only (docs/rpc.md, "Framing") ----
let buf = Buffer.alloc(0);
pi.stdout.on("data", (chunk) => {
  buf = Buffer.concat([buf, chunk]);
  let nl;
  while ((nl = buf.indexOf(0x0a)) >= 0) {
    const raw = buf.subarray(0, nl);
    buf = buf.subarray(nl + 1);
    const text = raw.toString("utf8").replace(/\r$/, "");
    if (!text) continue;
    let rec;
    try {
      rec = JSON.parse(text);
    } catch {
      continue;
    }
    handle(rec, text);
  }
});

function handle(rec, text) {
  if (rec.type === "response") {
    if (rec.id === "state") {
      // JSON mode began with the session header; the feed reads its cwd.
      process.stdout.write(JSON.stringify({ type: "session", version: 3, id: rec.data?.sessionId, timestamp: new Date().toISOString(), cwd: process.cwd() }) + "\n");
      running = true;
      send({ id: "prompt", type: "prompt", message: prompt });
    } else if (rec.id === "prompt") {
      if (!rec.success) {
        note(`Pi refused the prompt: ${one(rec.error, 400)}`);
        end();
      } else if (rec.data?.disposition === "handled") end();
      else {
        started = true;
        readInbox();
      }
    } else if (pending.has(rec.id)) {
      const entry = pending.get(rec.id);
      pending.delete(rec.id);
      if (!rec.success) note(`not ${entry.kind === "stop" ? "stopped" : "steered"} (${entry.id}): ${one(rec.error, 300)}`);
      else if (entry.kind === "stop") {
        note(`stopped (${entry.id})${entry.message ? `: ${one(entry.message)}` : ""}`);
        end();
      } else note(`steered (${entry.id}): ${one(entry.message)}`);
    } else if (!rec.success) note(`Pi refused ${rec.command}: ${one(rec.error, 300)}`);
    return;
  }
  if (rec.type === "extension_ui_request") {
    if (["select", "confirm", "input", "editor"].includes(rec.method)) send({ type: "extension_ui_response", id: rec.id, cancelled: true });
    return;
  }
  process.stdout.write(text + "\n");
  if (rec.type === "agent_settled") {
    running = false;
    end();
  }
}

// ---- the inbox ----
let pos = 0;
let rest = "";
function readInbox() {
  if (!started || ending || !existsSync(inbox)) return;
  const size = statSync(inbox).size;
  if (size <= pos) return;
  const fd = openSync(inbox, "r");
  const b = Buffer.alloc(size - pos);
  readSync(fd, b, 0, b.length, pos);
  closeSync(fd);
  pos = size;
  rest += b.toString("utf8");
  const lines = rest.split("\n");
  rest = lines.pop() ?? "";
  for (const line of lines) {
    if (!line.trim()) continue;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    const id = `inbox-${entry.id}`;
    if (!running) {
      note(`not ${entry.kind === "stop" ? "stopped" : "steered"} (${entry.id}): the run is not working`);
      continue;
    }
    pending.set(id, entry);
    if (entry.kind === "stop") {
      stopped = true;
      send({ id, type: "abort" });
    } else {
      // What a steer changes shows in the worker's visible text, readable in the log while the run goes (Owen, October 8:
      // a steer's effect had been visible only in reasoning no one reads, or later in the report).
      send({ id, type: "steer", message: `${String(entry.message ?? "")}\n\n(In your next message, say in a sentence or two what you will change because of this, or why nothing changes.)` });
    }
  }
}
let watcher;
try {
  watcher = watch(dirname(inbox), () => readInbox());
} catch {}
const poll = setInterval(readInbox, 1000);

// ---- lifecycle ----
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(sig, () => pi.kill(sig));
pi.on("exit", (code, signal) => {
  clearInterval(poll);
  watcher?.close();
  if (!ending && code === 0) note("Pi exited before its work settled");
  process.exitCode = stopped ? 7 : (code ?? (signal ? 1 : 0));
});
pi.on("error", (e) => {
  note(`could not start Pi: ${e.message}`);
  process.exitCode = 127;
});
send({ id: "state", type: "get_state" });
