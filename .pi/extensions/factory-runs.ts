/**
 * Tells the coordinator when a worker or verifier run finishes, so nobody has to.
 *
 * scripts/work and scripts/verify write each run to .runs/<run>.log and end it with "=== finished exit <n>". Outcomes
 * run in their own worktrees, so this watches .runs/ in the main checkout and in every worktree of the repository.
 * When a log gains that line, it sends the coordinator's session a message (which starts a turn, or follows the
 * current one) with the run, its exit status, the worktree's newest commit and, for a verifier, its WORKS and BROKEN
 * counts. Any long job can use the same channel: write .runs/<name>.log and end it with "=== finished exit <n>".
 * Only the interactive session (the coordinator) watches; workers and verifiers run Pi in JSON mode and skip it.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  let timer: NodeJS.Timeout | null = null;

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui" || timer) return;
    const git = (cwd: string, ...a: string[]) => {
      try {
        return execFileSync("git", a, { cwd, encoding: "utf8" }).trim();
      } catch {
        return "";
      }
    };
    const root = git(ctx.cwd, "rev-parse", "--show-toplevel");
    if (!root) return;
    const checkouts = () =>
      git(root, "worktree", "list", "--porcelain")
        .split("\n")
        .filter((l) => l.startsWith("worktree "))
        .map((l) => l.slice("worktree ".length));
    const finished = (file: string) => {
      const text = readFileSync(file, "utf8");
      const end = text.trimEnd().split("\n").pop() ?? "";
      return end.startsWith("=== finished") ? { first: text.split("\n")[0] ?? "", end } : null;
    };
    const logs = () =>
      checkouts().flatMap((dir) => {
        const runs = join(dir, ".runs");
        return existsSync(runs) ? readdirSync(runs).filter((f) => f.endsWith(".log")).map((f) => ({ dir, f, path: join(runs, f) })) : [];
      });
    // Runs already finished when the session starts were reported before.
    const told = new Set(logs().filter((l) => finished(l.path)).map((l) => l.path));

    const check = () => {
      for (const { dir, f, path } of logs()) {
        if (told.has(path)) continue;
        const done = finished(path);
        if (!done) continue;
        told.add(path);
        const run = f.slice(0, -4);
        const exit = done.end.replace("=== finished ", "");
        const where = dir === root ? "the main checkout" : dir.replace(`${root}/`, "");
        const lines = [`Run ${run} finished (${exit}) in ${where}. Newest commit: ${git(dir, "log", "-1", "--format=%h %s")}.`];
        const target = /^=== verifier on (\S+)/.exec(done.first)?.[1];
        if (target) {
          const seen = target.replace(/\.md$/, ".seen.md");
          if (existsSync(join(dir, seen))) {
            const list = readFileSync(join(dir, seen), "utf8").split("\n");
            const works = list.filter((l) => /^(- )?WORKS/.test(l)).length;
            const broken = list.filter((l) => /^(- )?BROKEN/.test(l)).length;
            lines.push(`The verifier's list ${seen}: ${works} WORKS, ${broken} BROKEN.`);
          }
        }
        lines.push(`Log: ${path}. Read it and carry on with the outcome.`);
        pi.sendMessage(
          { customType: "factory-run", content: lines.join("\n"), display: true },
          { triggerTurn: true, deliverAs: "followUp" },
        );
      }
    };
    timer = setInterval(check, 15_000);
    if (ctx.hasUI) ctx.ui.notify("Watching .runs/ in every worktree for finished runs", "info");
  });

  pi.on("session_shutdown", async () => {
    if (timer) clearInterval(timer);
    timer = null;
  });
}
