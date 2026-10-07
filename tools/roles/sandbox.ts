/**
 * The sandbox a worker or verifier runs in, so a role can only see and change its own workspace.
 *
 * scripts/work and scripts/verify load this with `pi -e`, and set ROLE_WORKSPACE to the role's workspace (also its
 * working directory), ROLE_ALLOW_READ to any other folders it must read (the Node install), and ROLE_ALLOW_WRITE to the
 * parts of the workspace it may change (default: all of it), each list separated by ":".
 *
 * - bash runs under the operating system's sandbox (sandbox-exec on macOS, through @anthropic-ai/sandbox-runtime):
 *   reading anything under /Users or the temporary folders is refused except the workspace and ROLE_ALLOW_READ,
 *   writing is refused outside ROLE_ALLOW_WRITE, and the only network is loopback (the role's own product). Its shell
 *   gets a folder beside the workspace as its home and temporary folder (outside the workspace's git repository: the
 *   product's credential store refuses to save inside one), no global git configuration, and the role's drive on its PATH.
 * - read refuses any path outside the workspace; write and edit refuse any path outside ROLE_ALLOW_WRITE.
 * - If the sandbox cannot start, bash refuses every command: a role never runs unconfined.
 */
import { execFileSync, spawn } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { SandboxManager } from "@anthropic-ai/sandbox-runtime";
import type { ExtensionAPI, BashOperations } from "@earendil-works/pi-coding-agent";
import { createBashTool } from "@earendil-works/pi-coding-agent";

const workspace = process.env.ROLE_WORKSPACE ? realpathSync(process.env.ROLE_WORKSPACE) : null;
const extraReads = (process.env.ROLE_ALLOW_READ ?? "").split(":").filter(Boolean);
/** The role's temporary folder: beside the workspace, so it is not inside the workspace's git repository. */
const scratch = workspace ? `${workspace}.tmp` : null;
const writable = workspace ? (process.env.ROLE_ALLOW_WRITE ?? "").split(":").filter(Boolean).map((p) => resolve(workspace, p)) : [];
if (workspace && writable.length === 0) writable.push(workspace);
/** macOS's /usr/bin/git is a shim that writes a cache in the user's temporary folder, which the sandbox denies; the
 * real git's folder goes first on the role's PATH. */
const realGit = (() => {
  try {
    return dirname(execFileSync("xcrun", ["--find", "git"], { encoding: "utf8" }).trim());
  } catch {
    return null;
  }
})();
const shellEnv = workspace
  ? {
      ...process.env,
      HOME: scratch!,
      TMPDIR: scratch!,
      GIT_CONFIG_GLOBAL: "/dev/null",
      GIT_CONFIG_NOSYSTEM: "1",
      PATH: [join(workspace, ".drive/bin"), realGit, process.env.PATH ?? ""].filter(Boolean).join(":"),
    }
  : process.env;

/** The real path of p, or of its nearest existing parent for a file not yet written. */
function real(p: string): string {
  let at = p;
  const rest: string[] = [];
  while (!existsSync(at) && dirname(at) !== at) {
    rest.unshift(at.slice(dirname(at).length + 1));
    at = dirname(at);
  }
  return join(realpathSync(at), ...rest);
}

function within(p: string, roots: string[]): boolean {
  if (!workspace) return false;
  const r = real(isAbsolute(p) ? p : resolve(workspace, p));
  return roots.some((root) => r === root || r.startsWith(root + "/"));
}

function sandboxedOps(): BashOperations {
  return {
    async exec(command, cwd, { onData, signal, timeout }) {
      // The sandbox library sets TMPDIR to its own shared folder; each command uses the role's own instead.
      const wrapped = await SandboxManager.wrapWithSandbox(scratch ? `export TMPDIR=${JSON.stringify(scratch)}; ${command}` : command);
      return new Promise((ok, no) => {
        const child = spawn("bash", ["-c", wrapped], { cwd, detached: true, stdio: ["ignore", "pipe", "pipe"], env: shellEnv });
        let timedOut = false;
        const kill = () => {
          try {
            if (child.pid) process.kill(-child.pid, "SIGKILL");
          } catch {
            child.kill("SIGKILL");
          }
        };
        const timer = timeout && timeout > 0 ? setTimeout(() => ((timedOut = true), kill()), timeout * 1000) : undefined;
        child.stdout?.on("data", onData);
        child.stderr?.on("data", onData);
        signal?.addEventListener("abort", kill, { once: true });
        child.on("error", (e) => (timer && clearTimeout(timer), no(e)));
        child.on("close", (code) => {
          if (timer) clearTimeout(timer);
          signal?.removeEventListener("abort", kill);
          if (signal?.aborted) no(new Error("aborted"));
          else if (timedOut) no(new Error(`timeout:${timeout}`));
          else ok({ exitCode: code });
        });
      });
    },
  };
}

export default function (pi: ExtensionAPI) {
  let ready = false;
  let why = "the sandbox has not started";
  const cwd = workspace ?? process.cwd();
  const bash = createBashTool(cwd, { operations: sandboxedOps() });

  pi.registerTool({
    ...bash,
    label: "bash (sandboxed to the workspace)",
    async execute(id, params, signal, onUpdate, ctx) {
      if (!ready) throw new Error(`bash is unavailable: ${why}`);
      return bash.execute(id, params, signal, onUpdate, ctx);
    },
  });

  pi.on("tool_call", async (event) => {
    if (!["read", "write", "edit"].includes(event.toolName)) return;
    const path = (event.input as { path?: string; file_path?: string }).path ?? (event.input as { file_path?: string }).file_path;
    if (typeof path !== "string") return;
    if (event.toolName === "read" && !within(path, [workspace!])) return { block: true, reason: `${path} is outside your workspace (${cwd})` };
    if (event.toolName !== "read" && !within(path, writable)) return { block: true, reason: `${path} is not yours to change; you may change ${writable.join(", ")}` };
  });

  pi.on("session_start", async () => {
    if (!workspace) return void (why = "ROLE_WORKSPACE is not set");
    try {
      (await import("node:fs")).mkdirSync(scratch!, { recursive: true });
      await SandboxManager.initialize({
        network: { allowedDomains: [], deniedDomains: [], allowLocalBinding: true },
        filesystem: {
          denyRead: ["/Users", realpathSync(tmpdir()), "/private/tmp", "/tmp"],
          // The machine's lock folder (lock files only): one full gates run at a time across all roles.
          allowRead: [workspace, scratch!, "/private/tmp/slope-locks", ...extraReads],
          allowWrite: [...writable, scratch!, "/private/tmp/slope-locks"],
          denyWrite: [],
        },
      });
      ready = true;
    } catch (e) {
      why = `the sandbox could not start (${e instanceof Error ? e.message : String(e)})`;
    }
  });

  pi.on("session_shutdown", async () => {
    if (ready) await SandboxManager.reset().catch(() => {});
  });
}
