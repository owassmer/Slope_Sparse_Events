/**
 * Before the coordinator delegates, a fresh reader says back what it would understand from the text.
 *
 * The first time this session runs scripts/work on a task, or scripts/steer with a message, the call is blocked. The
 * task (and, for a steer, the message as it arrives during that task) goes, with nothing else, to the worker's own
 * model, which says back in plain words what it would do, why it believes the work is wanted, and what done looks
 * like. The block shows that restatement beside the text. Where they differ, the text failed to carry its reason, and
 * the coordinator rewrites it; where they agree, the identical command goes through. A changed task or message is new
 * text and is read afresh. `scripts/steer --stop` and scripts/verify (the verifier gets Owen's outcome, not the
 * coordinator's words) pass untouched. One delegation goes per command, and a task is named as its file.
 *
 * The gate sees a command before it runs, so it cannot see everything: a loop's task paths, or an edit made in the same
 * round of tool calls (Pi checks every call of a round before running any). So the reader leaves word of each text it
 * read (.runs/read/<sha256>), and scripts/work and scripts/steer refuse a text it has not read.
 *
 * Nothing is delegated while Owen's answers or corrections are owed to the record (see memory.ts).
 *
 * Why (Owen, October 7): the coordinator kept handing workers rules where it meant reasons (the primitives' rules
 * instead of the organizing idea, failure cases a worker then wrote into the product's instructions). Asking it to re-read
 * its own text "as the one receiving it" did not hold: it ran the command again without re-reading. So the reading is
 * done by someone who actually knows nothing of why the work exists, the same kind of agent that will receive it.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { outstanding, owed } from "./memory.ts";
import { syncCheckouts } from "./factory-runs.ts";

const READER = `You have been handed the text below as a worker in a software repository you have not seen yet. Before you start, say back in a few plain sentences, without planning file changes:
1. What you would do.
2. Why you believe it is wanted: the reason behind it, as you understand it from the text.
3. What "done" looks like, and how you would know.
If the text leaves any of these unclear to you, say so plainly rather than guessing. Where the only reason the text gives
for something is that someone decided, approved or asked for it, name that part.`;

/** The command with heredoc bodies and quoted strings set aside, so a script is recognized only where the shell would
 * run it, not where a command merely mentions it. */
function invoked(command: string): string {
  const s = command.replace(/<<-?\s*(['"]?)(\w+)\1[^\n]*\n[\s\S]*?\n\s*\2\s*(?=\n|$)/g, "<<heredoc");
  // Quotes as the shell reads them: an apostrophe inside double quotes is a letter, not the start of a quoted string.
  let out = "", i = 0;
  while (i < s.length) {
    const c = s[i]!;
    if (c === "\\") { out += s.slice(i, i + 2); i += 2; continue; }
    if (c === "'") { const end = s.indexOf("'", i + 1); out += "''"; i = end < 0 ? s.length : end + 1; continue; }
    if (c === '"') {
      let j = i + 1;
      while (j < s.length && s[j] !== '"') j += s[j] === "\\" ? 2 : 1;
      out += '""'; i = j + 1; continue;
    }
    out += c; i++;
  }
  return out;
}

/** Where the fresh reader leaves word that it read a text: scripts/work and scripts/steer refuse a text without it, so
 * nothing is delegated unread however the command is put together (a loop, a chain, an edit made in the same round of
 * tool calls as the dispatch, which the gate sees before the edit is applied). */
function markRead(dir: string, text: string): void {
  let root = dir;
  try { root = execFileSync("git", ["-C", dir, "rev-parse", "--show-toplevel"], { encoding: "utf8" }).trim(); } catch { /* not a checkout */ }
  const marks = join(root, ".runs", "read");
  mkdirSync(marks, { recursive: true });
  writeFileSync(join(marks, createHash("sha256").update(text.trim()).digest("hex")), new Date().toISOString());
}

/** Runs of eight or more words a task shares with its outcome's latest verifier list. A task is written in the
 * coordinator's own words, so the worker gets what a user found, not the verifier's framing of it; copying is a matter
 * of form, so form is what this compares. It informs; it does not block. */
function copied(text: string, task: string, dir: string): string {
  const id = /tasks\/(\d+)-[\w.-]+\.md/.exec(task)?.[1];
  const seen = id ? join(dir, "outcomes", `${id}.seen.md`) : "";
  if (!seen || !existsSync(seen)) return "";
  const words = (t: string) => t.toLowerCase().replace(/[^a-z0-9$.' ]+/g, " ").split(/\s+/).filter(Boolean);
  const runs = (w: string[]) => new Set(w.slice(0, Math.max(0, w.length - 7)).map((_, i) => w.slice(i, i + 8).join(" ")));
  const theirs = runs(words(readFileSync(seen, "utf8")));
  const shared = [...runs(words(text))].filter((r) => theirs.has(r));
  return shared.length ? `\nIt shares ${shared.length} eight-word run(s) with the verifier's list (${seen}), e.g. "${shared.slice(0, 3).join('", "')}": the task should be in your own words.` : "";
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A task file's text as it will be when the command runs: written by this command (a heredoc), or as it stands. */
function taskText(command: string, task: string, dir: string): string | undefined {
  const written = new RegExp(`>\\s*${escape(task)}\\s*<<-?\\s*(['"]?)(\\w+)\\1[^\\n]*\\n([\\s\\S]*?)\\n\\s*\\2\\s*(?=\\n|$)`).exec(command);
  if (written) return written[3];
  const path = isAbsolute(task) ? task : join(dir, task);
  return existsSync(path) ? readFileSync(path, "utf8") : undefined;
}

/** For a steer: the message, and the task of the run it goes to. */
function steerText(command: string, dir: string): { task?: string; taskText?: string; message: string } | undefined {
  // The message as the shell hands it over: a single-quoted string as written, a double-quoted one with its escapes undone.
  const m = /scripts\/steer\s+(\S+)\s+(?:'([^']*)'|"((?:[^"\\]|\\.)*)")/.exec(command);
  if (!m) return undefined;
  const run = m[1];
  const message = m[2] ?? m[3]!.replace(/\\(["\\$`])/g, "$1");
  const runs = join(dir, ".runs");
  const log = existsSync(runs) ? readdirSync(runs).filter((f) => f.startsWith(run!) && f.endsWith(".log")).sort().pop() : undefined;
  const task = log ? /^=== worker on (\S+)/m.exec(readFileSync(join(runs, log), "utf8"))?.[1] : undefined;
  return { task, taskText: task ? taskText("", task, dir) : undefined, message: message! };
}

/** The worker's model, named in scripts/work and nowhere else. */
function workerModel(dir: string): { provider: string; id: string } | undefined {
  const script = join(dir, "scripts/work");
  const m = existsSync(script) ? /^WORKER="([^/"]+)\/([^":]+)/m.exec(readFileSync(script, "utf8")) : null;
  return m ? { provider: m[1]!, id: m[2]! } : undefined;
}

async function freshReading(ctx: ExtensionContext, dir: string, handed: string): Promise<string> {
  const named = workerModel(dir);
  const model = named ? ctx.modelRegistry.find(named.provider, named.id) : undefined;
  if (!model) return "(The fresh reader could not be reached: the worker's model is not available here. Read it yourself, as one who knows nothing of why it exists.)";
  try {
    const r = await ctx.modelRegistry.complete(model, { messages: [{ role: "user" as const, content: [{ type: "text" as const, text: `${READER}\n\n<handed>\n${handed}\n</handed>` }], timestamp: Date.now() }] }, { maxTokens: 1200, cacheRetention: "none" });
    const said = r.content.filter((c): c is { type: "text"; text: string } => c.type === "text").map((c) => c.text).join("").trim();
    return said || "(The fresh reader returned nothing.)";
  } catch (e) {
    return `(The fresh reader could not be reached: ${e instanceof Error ? e.message : String(e)}. Read it yourself, as one who knows nothing of why it exists.)`;
  }
}

export default function delegation(pi: ExtensionAPI) {
  const checked = new Set<string>();
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "bash") return;
    const command = String((event.input as { command?: string }).command ?? "");
    const shell = invoked(command);
    const at = /(?:^|[;&|(\n]\s*|\s)(?:\S*\/)?scripts\//;
    const works = [...shell.matchAll(new RegExp(at.source + "work\\s+(\\S+\\.md)", "g"))];
    const work = works[0];
    const steer = new RegExp(at.source + "steer\\s").test(shell) && !/--stop\b/.test(shell);
    if (!work && !steer) return;
    // One delegation per command, so the reader reads each one before it goes.
    if (works.length + (steer ? 1 : 0) > 1) return { block: true, reason: "Delegate one task (or one steer) per command, so the fresh reader reads each before it goes." };
    const dir = command.match(/^\s*cd\s+(\S+)/)?.[1] ?? ctx.cwd;

    // Nothing is delegated while Owen's answers or corrections are owed to the record (memory.ts says what is owed).
    const entries = ctx.sessionManager.getBranch() as Array<Record<string, any>>;
    const o = outstanding(entries, ctx.cwd);
    if (o.answers.length || o.corrections.length) return { block: true, reason: `Record what is owed before delegating:\n${owed(o, entries)}` };

    // A dispatch goes out on main's factory files: the checkout it runs from is brought into step first (scripts/sync).
    syncCheckouts(ctx.cwd, { force: true });

    // What the receiver will be handed, as it will read it.
    let handed: string;
    let task = "";
    if (work) {
      task = work[1]!;
      const text = /^[\w./-]+\.md$/.test(task) ? taskText(command, task, dir) : undefined;
      if (text === undefined) return { block: true, reason: `The fresh reader could not find the task "${task}" to read. Name the task file itself (scripts/work tasks/<id>-<n>.md), written before this command.` };
      handed = text;
    } else {
      const s = steerText(command, dir);
      if (!s) return;
      task = s.task ?? "";
      handed = `${s.taskText ? `${s.taskText}\n\n` : ""}While you are working on that, this message arrives from the one who handed it to you:\n${s.message}`;
    }
    const key = createHash("sha256").update(handed).digest("hex");
    if (checked.has(key)) return;
    checked.add(key);
    const reading = await freshReading(ctx, dir, handed);
    // What scripts/work or scripts/steer will check: the task's text, or the steer's message.
    if (work) markRead(dir, handed);
    else {
      markRead(dir, steerText(command, dir)?.message ?? "");
    }
    return {
      block: true,
      reason:
        `A fresh reader, the worker's own model knowing nothing else, was handed this and says:\n\n${reading}\n\n` +
        `Compare that with what you mean. Where the reason, the work or "done" came across differently, the text did not carry it: rewrite it. ` +
        `Where it came across as you mean it, run the identical command again.${copied(handed, task, dir)}\n\nWhat was handed over:\n\n${handed}`,
    };
  });
}
