/**
 * The coordinator's memory: what stays in its context, what is derived, what is carried across a compaction, and how
 * the past is reached again.
 *
 * Why (Owen, October 7). Pi's own compaction summarizes what happened, the state and what is next. For a coordinator
 * that lost two things: the state it wrote went stale (runs finished unreported, a retired task relaunched), and the
 * reasons behind Owen's corrections shrank to a list of constraints, followed after their basis had gone. So memory is
 * rebuilt from what each part of it is, not as one summary:
 *
 * - Prune: tool output is re-derivable from its source, and is most of a context's bulk. Once it is a few exchanges
 *   old it is replaced in context by a one-line stub (a context_edit; the raw entry stays in the session file). The
 *   correspondence with Owen stays whole far longer, and less is ever lost to a compaction.
 * - Derive: what the session itself records is read from its entries, exactly, by code: each question put to Owen
 *   with his answer (the ask tool makes these structured where they are given), each dispatch, steer and stop, each
 *   pull request opened, each reload. What the factory's records hold (runs, slots, branches, pull requests, issues)
 *   is read from them when needed: right after a compaction, and on /standing. None of it is written by a model, so
 *   none of it is paraphrased wrong or goes stale.
 * - Revise: the shared understanding (the reasons Owen and the coordinator arrived at, and what each owes the other)
 *   is held as items with ids. At a compaction, the compaction model sees the whole span since the last one, so it sees
 *   each correction in proportion, and returns edits to the items, never a new text; code applies them, so an item
 *   nobody touched is carried through word for word. Clef then checks, for each of Owen's messages in the span,
 *   whether its durable point is carried, and what is missed goes back for one more revision.
 * - Recall: the recall tool returns past exchanges verbatim by the entry ids the understanding cites, so a reason can
 *   be re-read in its original context rather than trusted as a paraphrase.
 *
 * Ported from Handoff's factory (October 8). Nothing in it is specific to Slope except the reading of its factory records
 * (factoryStanding) and where its Cloudflare credentials are kept (secret).
 */
import { execFile, execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, join } from "node:path";
import { promisify } from "node:util";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type Entry = Record<string, any>;
type Part = { type: string; text?: string; name?: string; id?: string; arguments?: Record<string, any> };
const parts = (content: unknown): Part[] => (typeof content === "string" ? [{ type: "text", text: content }] : Array.isArray(content) ? content : []);
const textOf = (content: unknown) => parts(content).filter((p) => p.type === "text" && p.text?.trim()).map((p) => p.text!.trim()).join("\n");

// ---------------------------------------------------------------- the session, read as a record

export interface Exchange { id: string; at: string; who: "owen" | "coordinator" | "notice" | "ask"; text: string }

/** What was said, in order: Owen's messages, the coordinator's words, run notices, and each question asked with the
 * answer given. Tool calls and their output are not dialogue. */
export function dialogue(entries: Entry[]): Exchange[] {
  const out: Exchange[] = [];
  const asks = new Map(ledger(entries).asks.map((a) => [a.callId, a]));
  for (const e of entries) {
    const at = String(e.timestamp ?? "");
    if (e.type === "custom_message") {
      const t = textOf(e.content);
      if (t) out.push({ id: e.id, at, who: "notice", text: t });
      continue;
    }
    const m = e.type === "message" ? e.message : undefined;
    if (!m) continue;
    if (m.role === "user") {
      const t = textOf(m.content);
      if (t) out.push({ id: e.id, at, who: "owen", text: t });
    } else if (m.role === "assistant") {
      const t = textOf(m.content);
      if (t) out.push({ id: e.id, at, who: "coordinator", text: t });
      for (const p of parts(m.content).filter((p) => p.type === "toolCall" && p.name === "ask")) {
        const a = asks.get(p.id!);
        if (a) out.push({ id: e.id, at, who: "ask", text: renderAsk(a) });
      }
    }
  }
  return out;
}

export interface Ask {
  callId: string; entryId: string; at: string;
  question: string; choices: Array<{ label: string; detail?: string }>; lean?: string; recordTo: string[];
  /** Why recordTo was corrected after the question was asked, if it was. */
  corrected?: string;
  answer?: { choice?: string; words?: string; at: string };
  /** Owen asked for a plainer explanation (or closed it): the question continues in the next ask. */
  continued?: boolean;
}
export interface Ledger {
  asks: Ask[];
  dispatches: Array<{ entryId: string; at: string; command: string; kind: "work" | "verify" | "steer" | "stop"; run?: string }>;
  pullRequests: Array<{ entryId: string; at: string; url: string }>;
  reloads: Array<{ entryId: string; at: string }>;
  modelChanges: Array<{ entryId: string; at: string; model: string }>;
  /** Files the coordinator wrote or edited with its write and edit tools. */
  filesWritten: Array<{ entryId: string; at: string; path: string; tool: string }>;
}
const renderAsk = (a: Ask) =>
  `Asked: ${a.question}\n${a.choices.map((c, i) => `  ${i + 1}. ${c.label}${c.detail ? ` (${c.detail})` : ""}`).join("\n")}${a.lean ? `\n  Lean: ${a.lean}` : ""}\n` +
  (a.answer ? `Owen: ${[a.answer.choice, a.answer.words].filter(Boolean).join(": ")}` : a.continued ? "Owen asked for a plainer explanation." : "Owen has not answered.") + (a.recordTo.length ? ` [to be recorded in ${a.recordTo.join(", ")}${a.corrected ? `; corrected: ${a.corrected}` : ""}]` : "");

/** What the session records exactly, read by code. */
export const CORRECTED = "record-corrected";

export function ledger(entries: Entry[]): Ledger {
  const L: Ledger = { asks: [], dispatches: [], pullRequests: [], reloads: [], modelChanges: [], filesWritten: [] };
  const calls = new Map<string, { name: string; args: Record<string, any>; entryId: string; at: string }>();
  let systems = 0;
  for (const e of entries) {
    // A recordTo named by mistake is corrected in the session, with its reason, never by writing a false record.
    if (e.type === "custom" && e.customType === CORRECTED) {
      const a = L.asks.find((x) => x.entryId === e.data?.entryId);
      if (a) { a.recordTo = e.data.recordTo; a.corrected = e.data.why; }
    }
    if (e.type === "model_change") L.modelChanges.push({ entryId: e.id, at: String(e.timestamp ?? ""), model: `${e.provider}/${e.modelId}` });
    const m = e.type === "message" ? e.message : undefined;
    if (!m) continue;
    const at = String(e.timestamp ?? "");
    if (m.role === "system" && systems++ > 0) L.reloads.push({ entryId: e.id, at });
    if (m.role === "assistant") for (const p of parts(m.content).filter((p) => p.type === "toolCall")) {
      calls.set(p.id!, { name: p.name!, args: p.arguments ?? {}, entryId: e.id, at });
      const a = p.arguments ?? {};
      if ((p.name === "write" || p.name === "edit") && a.path) L.filesWritten.push({ entryId: e.id, at, path: String(a.path), tool: p.name });
      if (p.name === "ask") L.asks.push({ callId: p.id!, entryId: e.id, at, question: a.question, choices: a.choices ?? [], lean: a.lean, recordTo: Array.isArray(a.recordTo) ? a.recordTo : a.recordTo ? [String(a.recordTo)] : [] });
    }
    if (m.role === "toolResult") {
      const call = calls.get(m.toolCallId);
      const t = textOf(m.content);
      if (call?.name === "ask") {
        const a = L.asks.find((x) => x.callId === m.toolCallId);
        const d = (m.details ?? {}) as { choice?: string; words?: string; talk?: boolean; explain?: boolean; dismissed?: boolean };
        if (a && (d.choice || d.words)) a.answer = { choice: d.choice, words: d.words, at };
        else if (a && (d.talk || d.explain || d.dismissed)) a.continued = true;
      }
      // What happened is read from what the scripts answered, not from what a command mentions.
      if (call?.name === "bash" && !m.isError) {
        const cmd = String(call.args.command ?? "");
        const line = (re: RegExp) => cmd.slice(cmd.search(re)).split("\n")[0]!.slice(0, 200);
        for (const run of t.match(/^run: \.runs\/(\S+)\.log/gm) ?? [])
          L.dispatches.push({ entryId: e.id, at, kind: run.includes("verify-") ? "verify" : "work", run: run.slice(11, -4), command: line(/scripts\/(work|verify)\s/) });
        if (/^steered \(/m.test(t)) L.dispatches.push({ entryId: e.id, at, kind: "steer", command: line(/scripts\/steer\s/) });
        if (/^stopped\b/m.test(t) && /scripts\/steer\s/.test(cmd)) L.dispatches.push({ entryId: e.id, at, kind: "stop", command: line(/scripts\/steer\s/) });
        if (/\bgh pr create\b/.test(cmd)) for (const url of new Set(t.match(/https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+/g) ?? [])) L.pullRequests.push({ entryId: e.id, at, url });
      }
    }
  }
  return L;
}

// ---------------------------------------------------------------- the understanding, revised by edits

export interface Item {
  id: string;
  /** What it governs: the product, how work is delegated and judged, how Owen and the coordinator work, or an open
   * commitment between them. */
  governs: string;
  text: string;
  /** owen: his own position. confirmed: the coordinator's framing Owen confirmed. reading: the coordinator's, not yet
   * confirmed. commitment: something one of them owes the other. */
  stands: "owen" | "confirmed" | "reading" | "commitment";
  sources: string[];
}
export type Edit =
  | { op: "keep"; id: string }
  | { op: "revise"; id: string; text: string; stands?: Item["stands"]; sources: string[] }
  | { op: "add"; governs: string; text: string; stands: Item["stands"]; sources: string[] }
  | { op: "merge"; ids: string[]; text: string; stands: Item["stands"]; sources: string[] }
  | { op: "retire"; id: string; why: string };

/** Applies edits to the items. An item no edit names is kept word for word: revision can only enter through an edit. */
export function applyEdits(items: Item[], edits: Edit[]): { items: Item[]; retired: Array<{ item: Item; why: string }> } {
  const byId = new Map(items.map((i) => [i.id, { ...i }]));
  const retired: Array<{ item: Item; why: string }> = [];
  const added: Item[] = [];
  const next = () => `u${randomUUID().slice(0, 8)}`;
  for (const e of edits) {
    if (e.op === "revise" && byId.has(e.id)) {
      const i = byId.get(e.id)!;
      byId.set(e.id, { ...i, text: e.text, stands: e.stands ?? i.stands, sources: [...new Set([...i.sources, ...e.sources])] });
    } else if (e.op === "retire" && byId.has(e.id)) {
      retired.push({ item: byId.get(e.id)!, why: e.why });
      byId.delete(e.id);
    } else if (e.op === "merge") {
      const from = e.ids.filter((id) => byId.has(id)).map((id) => byId.get(id)!);
      if (!from.length) continue;
      for (const f of from) byId.delete(f.id);
      added.push({ id: next(), governs: from[0]!.governs, text: e.text, stands: e.stands, sources: [...new Set([...from.flatMap((f) => f.sources), ...e.sources])] });
    } else if (e.op === "add") added.push({ id: next(), governs: e.governs, text: e.text, stands: e.stands, sources: e.sources });
  }
  // Order is kept: surviving items where they were, new ones at the end of their group.
  const result = [...items.filter((i) => byId.has(i.id)).map((i) => byId.get(i.id)!), ...added];
  const groups = [...new Set(result.map((i) => i.governs))];
  return { items: groups.flatMap((g) => result.filter((i) => i.governs === g)), retired };
}

const STANDS: Record<Item["stands"], string> = { owen: "Owen", confirmed: "the coordinator's framing, confirmed by Owen", reading: "the coordinator's reading, not yet confirmed", commitment: "owed" };
export function renderUnderstanding(items: Item[]): string {
  const groups = [...new Set(items.map((i) => i.governs))];
  return groups.map((g) => `### ${g}\n${items.filter((i) => i.governs === g).map((i) => `- [${i.id}] ${i.text} (${STANDS[i.stands]}; ${i.sources.join(", ")})`).join("\n")}`).join("\n\n");
}

export const REVISION = `You revise the understanding a coordinator carries across a compaction of its conversation with Owen. The coordinator delegates Owen's product work to workers and verifiers, and is judged by how rarely Owen has to restore the purpose to it. After this compaction it continues with this understanding, the most recent turns, its standing instructions, the records it can read, a ledger of what the session recorded exactly (questions asked with Owen's answers, dispatches, pull requests, reloads) and a recall tool that returns any past exchange verbatim by its entry id. So do not record what happened, and do not record state (runs, branches, files, pull requests): those are read exactly from their records. Record only the understanding.

The understanding is the reasons Owen and the coordinator arrived at (about the product, about how work is delegated and judged, about how they work together) and what each currently owes the other. It is held as items. You see the current items and the whole conversation since the last compaction, so you can see each part of it in proportion to the whole; return edits to the items as JSON, never a new text.

- keep an item that still holds as it is (you may omit keeps: an item no edit names is kept word for word).
- revise an item the conversation refined or corrected; name the entry ids that changed it.
- add an item for a reason or commitment the conversation established that no item holds.
- merge items that turn out to be one reason.
- retire an item whose basis has gone, or a commitment that was met, saying why.

Each item is a reason, not a rule: why it holds and what it serves, with the scope it was given. A rule without its reason gets followed after its basis is gone, which is the failure this exists to prevent. Read each of Owen's corrections for what it did: corrected one action, supplied a fact, set a lasting constraint, or moved the destination; a correction of one action that has since been applied is not an item. Mark where each item stands: "owen" (his own position), "confirmed" (the coordinator's framing Owen confirmed, often the clearer statement of what he meant), "reading" (the coordinator's, not yet confirmed), or "commitment" (something one owes the other, still open). Use Owen's words where his wording carries the meaning. Cite the entry ids (shown as [id]) each item rests on. Group items by what they govern with short group names; reuse the existing names. Leave out the history of mistakes (the notebook holds those) and what a file already holds in full (the purpose in AGENTS.md, outcomes in outcomes/, decisions in design/decisions.md); point to those where they bear on an item.

Answer with only a JSON array of edits, each one of:
{"op":"keep","id":"..."}
{"op":"revise","id":"...","text":"...","stands":"...","sources":["..."]}
{"op":"add","governs":"...","text":"...","stands":"...","sources":["..."]}
{"op":"merge","ids":["...","..."],"text":"...","stands":"...","sources":["..."]}
{"op":"retire","id":"...","why":"..."}`;

export function revisionRequest(items: Item[], span: Exchange[], missed: Exchange[] = []): string {
  const convo = span.map((x) => `[${x.id}] ${x.who.toUpperCase()} ${x.at.slice(0, 16)}\n${x.text}`).join("\n\n");
  return `${REVISION}\n\n<items>\n${items.length ? renderUnderstanding(items) : "(none yet)"}\n</items>\n\n<conversation>\n${convo}\n</conversation>` +
    (missed.length ? `\n\nAn independent check found the durable point of these messages of Owen's not carried by the items. Revise so it is, or retire what they superseded:\n${missed.map((x) => `[${x.id}] ${x.text}`).join("\n\n")}` : "");
}

export function parseEdits(text: string): Edit[] {
  const json = text.slice(text.indexOf("["), text.lastIndexOf("]") + 1);
  const edits = JSON.parse(json) as Edit[];
  if (!Array.isArray(edits)) throw new Error("the revision is not a list of edits");
  return edits;
}

// ---------------------------------------------------------------- the independent check (Clef)

/** A Cloudflare credential: from the environment, else the main checkout's .env (where Slope's own Clef calls read it). */
async function secret(name: string): Promise<string | null> {
  if (process.env[name]) return process.env[name]!;
  try {
    const common = (await promisify(execFile)("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"])).stdout.trim();
    const env = readFileSync(join(common, "..", ".env"), "utf8");
    const m = new RegExp(`^\\s*(?:export\\s+)?${name}\\s*=\\s*["']?([^"'\\n]*)`, "m").exec(env);
    return m?.[1]?.trim() || null;
  } catch {
    return null;
  }
}

/** For each of Owen's messages in the span, Clef judges whether its durable point is carried by the understanding. One
 * choice per message, every message checked. Returns the messages judged not carried; null if Clef could not be asked. */
export async function uncarried(items: Item[], span: Exchange[], signal?: AbortSignal): Promise<Exchange[] | null> {
  const owen = span.filter((x) => x.who === "owen" || (x.who === "ask" && /\nOwen: /.test(x.text)));
  if (!owen.length) return [];
  const [account, token] = await Promise.all([secret("CLOUDFLARE_ACCOUNT_ID"), secret("CLOUDFLARE_API_TOKEN")]);
  if (!account || !token) return null;
  // Clef takes at most 64 questions a request, so the span is checked in batches: every message is checked, and the
  // newest (past the first 64) are the ones that matter most.
  const missed: Exchange[] = [];
  try {
    for (let start = 0; start < owen.length; start += 64) {
      const asked = owen.slice(start, start + 64);
      const questions = Object.fromEntries(asked.map((x, i) => [`m${i}`, {
        type: "choice",
        instructions: `Message ${x.id} from Owen, in state.messages: does state.understanding carry what in it lasts beyond the moment it was said?`,
        criteria: {
          carried: "Something in it lasts (a reason, a constraint, a change of direction, a commitment) and the understanding states it, or something that clearly includes it.",
          not_carried: "Something in it lasts, and the understanding does not state it.",
          momentary: "Nothing in it lasts beyond its moment: an approval of one step, a go-ahead, a status question or a remark whose effect is already done.",
        },
      }]));
      const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/@cf/cloudflare/clef`, {
        method: "POST", signal,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: JSON.stringify({ model: "clef", state: { understanding: renderUnderstanding(items), messages: asked.map((x) => ({ id: x.id, text: x.text })) }, questions }),
      });
      const body = (await res.json()) as any;
      const answers = (body?.result ?? body)?.answers ?? {};
      // A missing answer is "not checked", never "carried".
      if (asked.some((_, i) => !answers[`m${i}`]?.probabilities)) return null;
      missed.push(...asked.filter((_, i) => {
        const p = answers[`m${i}`].probabilities;
        return Number(p.not_carried) > Math.max(Number(p.carried), Number(p.momentary));
      }));
    }
    return missed;
  } catch {
    return null;
  }
}


// ---------------------------------------------------------------- what must be recorded before a turn settles

/** Owen's messages since recording was first enforced in this session, with Clef's reading of whether each corrects the
 * coordinator. A correction is the coordinator's measure, so it goes in the notebook, citing the message. */
export const READING = "owen-reading";
export const ACTIVATED = "recording-enforced";

export async function readOwen(entries: Entry[], signal?: AbortSignal): Promise<Array<{ entryId: string; yes: number; notRecorded: number }> | null> {
  const start = entries.findIndex((e) => e.type === "custom" && e.customType === ACTIVATED);
  if (start < 0) return [];
  const read = new Set(entries.filter((e) => e.type === "custom" && e.customType === READING).map((e) => e.data?.entryId));
  const unread: Array<{ id: string; text: string; before: string }> = [];
  let lastCoordinator = "";
  entries.forEach((e, i) => {
    const m = e.type === "message" ? e.message : undefined;
    if (m?.role === "assistant") { const t = textOf(m.content); if (t) lastCoordinator = t; }
    if (i > start && m?.role === "user" && !read.has(e.id)) {
      const t = textOf(m.content);
      if (t) unread.push({ id: e.id, text: t, before: lastCoordinator.slice(-3000) });
    }
  });
  if (!unread.length) return [];
  const [account, token] = await Promise.all([secret("CLOUDFLARE_ACCOUNT_ID"), secret("CLOUDFLARE_API_TOKEN")]);
  if (!account || !token) return null;
  const out: Array<{ entryId: string; yes: number; notRecorded: number }> = [];
  for (const x of unread) {
    try {
      const res = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/@cf/cloudflare/clef`, {
        method: "POST", signal, headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: JSON.stringify({
          model: "clef",
          state: { coordinatorSaid: x.before, owenReplied: x.text },
          questions: {
            corrects: { type: "noul", instructions: "Owen supervises the coordinator, an agent that delegates his product work. Does Owen's reply correct how the coordinator worked or reasoned: point out a mistake, a wrong posture or reading, or something done short of what was meant?" },
            notRecorded: { type: "noul", instructions: "Does Owen say that what he points out should not be recorded or noted?" },
          },
        }),
      });
      const body = (await res.json()) as any;
      // A noul answer is one probability that the proposition holds.
      const answers = (body?.result ?? body)?.answers ?? {};
      const yes = answers.corrects?.noul;
      const notRecorded = answers.notRecorded?.noul;
      if (typeof yes !== "number" || typeof notRecorded !== "number") return null;
      out.push({ entryId: x.id, yes, notRecorded });
    } catch {
      return null;
    }
  }
  return out;
}

const roots = (cwd: string) => [cwd, ...run("git", ["worktree", "list", "--porcelain"], cwd).split("\n").filter((l) => l.startsWith("worktree ")).map((l) => l.slice(9))];

/** What is owed to the record now: answers not yet in the files they named, and corrections with no notebook note citing
 * them (in any checkout, including the one scripts/record uses for main). */
export function outstanding(entries: Entry[], cwd: string): { answers: Ask[]; corrections: string[] } {
  const L = ledger(entries);
  const rs = roots(cwd);
  const changedSince = (path: string, at: string) => rs.some((r) => existsSync(join(r, path)) && statSync(join(r, path)).mtimeMs > Date.parse(at));
  const exists = (path: string) => rs.some((r) => existsSync(join(r, path)));
  const answers = L.asks.filter((a) => a.answer && a.recordTo.some((path) => exists(path) && !changedSince(path, a.answer!.at)));
  const notebooks = rs.map((r) => join(r, "notebook.md")).filter((f) => existsSync(f)).map((f) => readFileSync(f, "utf8")).join("\n");
  const corrections = entries
    // Owed: a correction Owen has not said to leave unrecorded (both read by Clef; neither is the coordinator's call).
    .filter((e) => e.type === "custom" && e.customType === READING && e.data?.yes >= 0.5 && !(e.data?.notRecorded >= 0.5))
    .map((e) => String(e.data.entryId))
    .filter((id) => !notebooks.includes(id));
  return { answers, corrections };
}

export function owed(o: { answers: Ask[]; corrections: string[] }, entries: Entry[]): string {
  const said = new Map(dialogue(entries).filter((x) => x.who === "owen").map((x) => [x.id, x.text]));
  const lines: string[] = [];
  for (const a of o.answers) lines.push(`- Owen's answer to "${a.question.slice(0, 120)}" (entry ${a.entryId}) is not yet in ${a.recordTo.join(", ")}.`);
  for (const id of o.corrections) lines.push(`- Owen's correction (entry ${id}): "${(said.get(id) ?? "").slice(0, 200)}" has no notebook note. Record what happened as an observation: scripts/record note --from ${id} "<line>".`);
  return lines.join("\n");
}

// ---------------------------------------------------------------- the factory's records

const run = (cmd: string, args: string[], cwd: string) => {
  try {
    return execFileSync(cmd, args, { cwd, encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
};

/** Where the factory stands, read from its records now. */
export async function factoryStanding(cwd: string): Promise<string> {
  const lines = ["## Where the factory stands (read from the records just now)"];
  const checkouts = run("git", ["worktree", "list", "--porcelain"], cwd).split("\n\n")
    .map((b) => ({ path: /^worktree (.*)$/m.exec(b)?.[1], branch: /^branch refs\/heads\/(.*)$/m.exec(b)?.[1] }))
    .filter((c) => c.path && existsSync(c.path));
  const main = checkouts[0]?.path ?? cwd;
  let stateOf: ((log: string) => { state: string }) | undefined;
  try {
    stateOf = (await import(join(main, "tools/feed/runs.mjs"))).runStateOf;
  } catch {}
  const going: string[] = [];
  lines.push("Checkouts:");
  for (const c of checkouts) {
    lines.push(`- ${basename(c.path!)} [${c.branch ?? "detached"}] ${run("git", ["log", "-1", "--format=%h %s"], c.path!).slice(0, 110)}`);
    const runs = join(c.path!, ".runs");
    if (stateOf && existsSync(runs)) for (const f of readdirSync(runs).filter((f) => f.endsWith(".log"))) {
      const s = stateOf(join(runs, f));
      if (s.state !== "finished") going.push(`- ${f.slice(0, -4)} in ${basename(c.path!)}: ${s.state}`);
    }
  }
  lines.push(going.length ? "Runs going:" : "Runs going: none.", ...going);
  const locks = "/private/tmp/slope-locks";
  if (existsSync(locks)) lines.push(`Machine slots held: ${readdirSync(locks).join(", ") || "none"}.`);
  const list = (args: string[], label: string) => {
    const out = run("gh", [...args, "--json", "number,title", "--jq", '.[] | "- #\\(.number) \\(.title)"'], main);
    lines.push(out ? `${label}:` : `${label}: none.`, ...(out ? out.split("\n") : []));
  };
  list(["pr", "list", "--state", "open"], "Open pull requests");
  list(["issue", "list", "--label", "needs-owen", "--state", "open"], "Open needs-owen issues");
  return lines.join("\n");
}

/** How each run this session dispatched ended, from its log in whichever checkout holds it. */
export function dispatchOutcomes(L: Ledger, cwd: string): Array<{ run: string; kind: string; at: string; ended: string }> {
  const roots = [cwd, ...run("git", ["worktree", "list", "--porcelain"], cwd).split("\n").filter((l) => l.startsWith("worktree ")).map((l) => l.slice(9))];
  return L.dispatches.filter((d) => d.run).map((d) => {
    const log = roots.map((r) => join(r, ".runs", `${d.run}.log`)).find((f) => existsSync(f));
    let ended = "log not found";
    if (log) {
      const text = readFileSync(log, "utf8");
      ended = /^=== finished:? ?(.*)$/m.exec(text)?.[1]?.trim() || "still going";
    }
    return { run: d.run!, kind: d.kind, at: d.at, ended };
  });
}

/** The session's own open items, read exactly: questions Owen has not answered, answers whose record is not written. */
export function openFromLedger(L: Ledger, cwd: string): string {
  const unanswered = L.asks.filter((a) => !a.answer && !a.continued);
  const lines = ["## Open in the session (read from the session's entries)"];
  lines.push(unanswered.length ? "Questions to Owen not yet answered:" : "Questions to Owen not yet answered: none.", ...unanswered.map((a) => `- [${a.entryId}] ${a.question}`));
  // An answer meant for a file is recorded once that file has changed since the answer, in any checkout (records go to
  // main through their own checkout).
  const roots = run("git", ["worktree", "list", "--porcelain"], cwd).split("\n").filter((l) => l.startsWith("worktree ")).map((l) => l.slice(9));
  const changedSince = (path: string, at: string) => [cwd, ...roots].some((r) => existsSync(join(r, path)) && statSync(join(r, path)).mtimeMs > Date.parse(at));
  // Only real repository paths are checked (questions asked before recordTo became paths named it in prose).
  const exists = (path: string) => [cwd, ...roots].some((r) => existsSync(join(r, path)));
  const unrecorded = L.asks.filter((a) => a.answer && a.recordTo.some((path) => exists(path) && !changedSince(path, a.answer!.at)));
  if (unrecorded.length) lines.push("Answers not yet recorded where they were to go:", ...unrecorded.map((a) => `- [${a.entryId}] ${a.question} → ${a.recordTo.filter((path) => exists(path) && !changedSince(path, a.answer!.at)).join(", ")}`));
  return lines.join("\n");
}

// ---------------------------------------------------------------- the extension

const KEEP_SPANS = 3; // tool output from the last three exchanges with Owen stays in context
// Pruning changes earlier messages, and a provider that reuses a live session (claude-cli) then starts afresh with the
// whole transcript, uncached. So it happens rarely and in bulk: only once this much old output has gathered.
const PRUNE_AT_CHARS = 200_000;
const STUB = (name: string, what: string) => `(${name} output dropped from context: ${what}. It is in the session file; re-derive it from its source if needed.)`;

export default function memory(pi: ExtensionAPI) {
  pi.registerTool({
    name: "ask",
    label: "Ask Owen",
    description: "Put a question to Owen with choices and your lean. He picks one or answers in his own words; the answer is recorded structured in the session, so it is never parsed from prose. Use it for every question whose answer decides work.",
    parameters: Type.Object({
      question: Type.String({ description: "The question, framed plainly with what is at stake." }),
      choices: Type.Array(Type.Object({ label: Type.String(), detail: Type.Optional(Type.String()) }), { minItems: 2 }),
      lean: Type.String({ description: "Which choice you lean toward, and why." }),
      recordTo: Type.Array(Type.String(), { description: "The repository files the answer will be recorded in (e.g. design/decisions.md, outcomes/003.md, tasks/003-12.md, notebook.md); empty when it decides this action only." }),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const own = "Answer in my own words";
      const talk = "Explain this more plainly first";
      const labels = params.choices.map((c, i) => `${i + 1}. ${c.label}${c.detail ? ` (${c.detail})` : ""}`);
      if (!ctx.hasUI) return { content: [{ type: "text", text: "No one can answer here: ask in prose instead." }], details: {} };
      const picked = await ctx.ui.select(`${params.question}\n\nLean: ${params.lean}`, [...labels, own, talk]);
      // Talking it through is answering in his own words; this asks for the question itself to be made clearer.
      if (picked === talk) return { content: [{ type: "text", text: "Owen wants this explained more plainly before he answers. Expand on the question and each choice in plain, intuitive words (what each would mean in practice, and why you lean as you do), then ask again." }], details: { explain: true } };
      if (picked === undefined) return { content: [{ type: "text", text: "Owen closed the question without answering. Wait for his message." }], details: { dismissed: true } };
      const choice = picked === own ? undefined : params.choices[labels.indexOf(picked)]?.label;
      const words = picked === own ? await ctx.ui.editor(params.question) : await ctx.ui.input("Anything to add? (Enter to skip)");
      const answer = [choice, words?.trim()].filter(Boolean).join(": ");
      return { content: [{ type: "text", text: `Owen: ${answer || "(no answer)"}` }], details: { choice, words: words?.trim() || undefined } };
    },
  });

  pi.registerTool({
    name: "correct_record",
    label: "Correct where an answer is recorded",
    description: "When a question named the wrong files in recordTo (a mistaken path or number), say where its answer is actually recorded and why the first naming was wrong. The correction and its reason stay in the session and show in the ledger. Never use it to move an answer away from where it belongs.",
    parameters: Type.Object({
      entryId: Type.String({ description: "The entry id of the ask, as the ledger or the recording notice gives it." }),
      recordTo: Type.Array(Type.String(), { description: "The repository files the answer is actually recorded in." }),
      why: Type.String({ description: "Why the first naming was wrong." }),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const asked = ledger(ctx.sessionManager.getBranch() as Entry[]).asks.find((a) => a.entryId === params.entryId);
      if (!asked) return { content: [{ type: "text", text: `No question with entry id ${params.entryId}.` }], details: {} };
      pi.appendEntry(CORRECTED, { entryId: params.entryId, recordTo: params.recordTo, why: params.why, at: new Date().toISOString() });
      return { content: [{ type: "text", text: `Recorded: the answer to "${asked.question.slice(0, 100)}" belongs in ${params.recordTo.join(", ")} (was ${asked.recordTo.join(", ")}), because ${params.why}` }], details: {} };
    },
  });

  pi.registerTool({
    name: "recall",
    label: "Recall",
    description: "Return past exchanges with Owen verbatim (his messages, your words, notices, questions and answers), by the entry ids the understanding cites (with the exchanges around them), or by a time range. Use it before acting on a reason whose context matters.",
    parameters: Type.Object({
      ids: Type.Optional(Type.Array(Type.String(), { description: "Entry ids, as cited in the understanding or the ledger." })),
      around: Type.Optional(Type.Number({ description: "Also return this many exchanges before and after each id (default 1)." })),
      from: Type.Optional(Type.String({ description: "Or every exchange from this time (ISO, e.g. 2026-10-07T18:00)." })),
      to: Type.Optional(Type.String({ description: "Up to this time (ISO); with from." })),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const all = dialogue(ctx.sessionManager.getBranch() as Entry[]);
      const n = params.around ?? 1;
      const want = new Set<number>();
      if (params.from) all.forEach((x, k) => { if (x.at >= params.from! && (!params.to || x.at <= params.to)) want.add(k); });
      for (const id of params.ids ?? []) {
        const i = all.findIndex((x) => x.id === id);
        for (let k = Math.max(0, i - n); i >= 0 && k <= Math.min(all.length - 1, i + n); k++) want.add(k);
      }
      const text = [...want].sort((a, b) => a - b).map((k) => `[${all[k]!.id}] ${all[k]!.who.toUpperCase()} ${all[k]!.at.slice(0, 16)}\n${all[k]!.text}`).join("\n\n");
      return { content: [{ type: "text", text: text || "No exchange with those ids." }], details: {} };
    },
  });

  // Prune: tool output older than the last few exchanges with Owen leaves the context.
  const pruned = new Set<string>();
  pi.on("turn_end", async (_event, ctx) => {
    const branch = ctx.sessionManager.getBranch() as Entry[];
    const owenAt: number[] = [];
    branch.forEach((e, i) => e.type === "message" && e.message?.role === "user" && owenAt.push(i));
    const cutoff = owenAt.length > KEEP_SPANS ? owenAt[owenAt.length - KEEP_SPANS]! : -1;
    if (cutoff < 0) return;
    const calls = new Map<string, Part>();
    const entries: Array<{ type: "context_edit"; targetId: string; replacement: { content: string } }> = [];
    let size = 0;
    // Already pruned: what the session's own edits say, not a memory of having asked.
    for (const e of branch) if (e.type === "context_edit") pruned.add(e.targetId);
    branch.forEach((e, i) => {
      const m = e.type === "message" ? e.message : undefined;
      if (m?.role === "assistant") for (const p of parts(m.content)) if (p.type === "toolCall") calls.set(p.id!, p);
      if (i >= cutoff || m?.role !== "toolResult" || pruned.has(e.id)) return;
      const call = calls.get(m.toolCallId);
      if (call?.name === "ask" || call?.name === "recall") return;
      const what = String(call?.arguments?.command ?? call?.arguments?.path ?? call?.name ?? "tool").replace(/\s+/g, " ").slice(0, 120);
      size += JSON.stringify(m.content ?? "").length;
      entries.push({ type: "context_edit", targetId: e.id, replacement: { content: STUB(call?.name ?? "tool", what) } });
    });
    if (size < PRUNE_AT_CHARS) return;
    return { entries };
  });

  pi.on("session_before_compact", async (event, ctx) => {
    const { preparation, branchEntries, signal } = event;
    const model = ctx.model;
    if (!model) return;
    const previous = [...(branchEntries as Entry[])].reverse().find((e) => e.type === "compaction" && e.details?.understanding)?.details?.understanding as Item[] | undefined;
    const keptFrom = (branchEntries as Entry[]).findIndex((e) => e.id === preparation.firstKeptEntryId);
    const lastCompaction = (branchEntries as Entry[]).map((e) => e.type).lastIndexOf("compaction");
    const span = dialogue((branchEntries as Entry[]).slice(lastCompaction + 1, keptFrom < 0 ? undefined : keptFrom));
    const ask = async (request: string) => {
      const r = await ctx.modelRegistry.complete(model, { messages: [{ role: "user" as const, content: [{ type: "text" as const, text: request }], timestamp: Date.now() }] }, { maxTokens: 16_000, signal, cacheRetention: "none", sessionId: randomUUID() });
      return { text: r.content.filter((c): c is { type: "text"; text: string } => c.type === "text").map((c) => c.text).join(""), usage: r.usage };
    };
    try {
      let items = previous ?? [];
      const first = await ask(revisionRequest(items, span));
      let { items: revised, retired } = applyEdits(items, parseEdits(first.text));
      const missed = await uncarried(revised, span, signal);
      if (missed?.length) {
        const second = await ask(revisionRequest(revised, span, missed));
        const again = applyEdits(revised, parseEdits(second.text));
        revised = again.items;
        retired = [...retired, ...again.retired];
      }
      items = revised;
      const L = ledger(branchEntries as Entry[]);
      const file = ctx.sessionManager.getSessionFile();
      const summary = [
        "## The understanding (revised at each compaction; cite and recall entry ids to re-read the original)",
        renderUnderstanding(items),
        openFromLedger(L, ctx.cwd),
        `The full conversation is in Pi's session file${file ? ` (${file})` : ""}; recall returns any exchange verbatim. Where the factory stands follows this, read from the records.`,
        missed === null ? "(Clef could not be asked to check this revision.)" : "",
      ].filter(Boolean).join("\n\n");
      return { compaction: { summary, firstKeptEntryId: preparation.firstKeptEntryId, tokensBefore: preparation.tokensBefore, usage: first.usage, details: { understanding: items, retired, checked: missed !== null, missed: missed?.map((x) => x.id) ?? [] } } };
    } catch (e) {
      ctx.ui.notify(`The coordinator's compaction failed (${e instanceof Error ? e.message : String(e)}); Pi's own compaction runs instead.`, "warning");
      return;
    }
  });

  const showStanding = async (cwd: string, entries: Entry[]) => {
    const L = ledger(entries);
    const records = roots(cwd).find((r) => r.endsWith("-records"));
    const unmerged = records ? run("git", ["-C", records, "diff", "origin/main..HEAD", "--", "notebook.md"], cwd).split("\n").filter((l) => l.startsWith("+- ")).map((l) => l.slice(1, 220)) : [];
    const recent = dispatchOutcomes(L, cwd).slice(-10).map((d) => `- ${d.run}: ${d.ended}`);
    const o = outstanding(entries, cwd);
    const content = [
      await factoryStanding(cwd),
      openFromLedger(L, cwd),
      `## This session's recent dispatches, and how each ended\n${recent.join("\n") || "- none"}`,
      `## Notebook lines recorded but not yet merged into main\n${unmerged.join("\n") || "- none"}`,
      o.answers.length || o.corrections.length ? `## Owed to the record\n${owed(o, entries)}` : "",
    ].filter(Boolean).join("\n\n");
    pi.sendMessage({ customType: "factory-standing", content, display: true }, { triggerTurn: false });
  };
  pi.on("session_compact", async (_event, ctx) => showStanding(ctx.cwd, ctx.sessionManager.getBranch() as Entry[]));

  // Recording is not left to remembering (Owen, October 7): before a turn settles, Owen's new messages are read for
  // corrections, and whatever is owed to the record holds the turn, once per item, with what to record and where. The
  // delegation gate refuses to dispatch while anything is owed.
  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    const entries = ctx.sessionManager.getBranch() as Entry[];
    if (!entries.some((e) => e.type === "custom" && e.customType === ACTIVATED)) pi.appendEntry(ACTIVATED, { at: new Date().toISOString() });
  });
  const held = new Set<string>();
  pi.on("agent_before_settle", async (event, ctx) => {
    if (ctx.mode !== "tui") return undefined;
    let entries = ctx.sessionManager.getBranch() as Entry[];
    const readings = await readOwen(entries);
    for (const r of readings ?? []) pi.appendEntry(READING, r);
    if (readings?.length) entries = ctx.sessionManager.getBranch() as Entry[];
    const o = outstanding(entries, ctx.cwd);
    const fresh = { answers: o.answers.filter((a) => !held.has(a.entryId)), corrections: o.corrections.filter((id) => !held.has(id)) };
    if (!fresh.answers.length && !fresh.corrections.length) return undefined;
    for (const a of fresh.answers) held.add(a.entryId);
    for (const id of fresh.corrections) held.add(id);
    return {
      entries: [{ type: "custom_message", customType: "recording-owed", display: true, content: `Before this turn ends, record what is owed:\n${owed(fresh, entries)}` }],
      continue: true,
    };
  });
  pi.registerCommand("standing", {
    description: "Where the factory stands and what is open in the session, read from the records now",
    handler: async (_args, ctx) => showStanding(ctx.cwd, ctx.sessionManager.getBranch() as Entry[]),
  });
}
