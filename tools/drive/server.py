# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.48,<2"]
# ///
"""drive's server: the running product and a browser on it, for one workspace.

`drive start --workspace <dir>` starts this outside any role's sandbox. It serves the product's web viewer from the
workspace's code on a free port, opens a headless Chrome on it, and answers drive's commands on a control port on
127.0.0.1, so a role's drive (which has no dependencies and runs inside the role's sandbox) reaches only its own product.

Everything it starts runs with no route to a paid call: judgment-model requests are answered only from saved responses
(SLOPE_JEV_CACHE_ONLY=1), and the AWS, Claude and GitHub credentials are hidden. The workspace sits outside the main
checkout, so the product finds no .env either.

The state (control port, product url, pids) goes to the state file drive passes (DRIVE_STATE). A server whose workspace
is gone shuts down.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HIDDEN_ENV = ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "GH_TOKEN",
              "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENROUTER_API_KEY",
              "CLOUDFLARE_API_TOKEN", "TYPESAFE_API_KEY", "VIRTUAL_ENV")
TEXT_LIMIT = 6000
SEE_JS = (HERE / "see.js").read_text()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Product:
    def __init__(self, workspace: Path, state_file: Path):
        self.ws = workspace
        self.state_file = state_file
        self.home = state_file.parent
        self.home.mkdir(parents=True, exist_ok=True)
        for sub in ("no-claude", "no-gh"):
            (self.home / sub).mkdir(exist_ok=True)
        self.viewer: subprocess.Popen | None = None
        self.url = ""
        self.errors: list[str] = []
        from playwright.sync_api import sync_playwright

        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            self.browser = self.pw.chromium.launch(headless=True)
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.page.on("console", lambda m: self.errors.append(f"console {m.type}: {m.text} ({(m.location or {}).get('url', '')})")
                     if m.type in ("error", "warning") else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"page error: {e}"))
        self.page.on("response", lambda r: self.errors.append(f"HTTP {r.status} {r.request.method} {r.url}")
                     if r.status >= 400 else None)

    # --- the product -------------------------------------------------------------------------------------------
    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in HIDDEN_ENV}
        env.update({"SLOPE_JEV_CACHE_ONLY": "1", "AWS_CONFIG_FILE": os.devnull,
                    "AWS_SHARED_CREDENTIALS_FILE": os.devnull, "AWS_EC2_METADATA_DISABLED": "true",
                    "CLAUDE_CONFIG_DIR": str(self.home / "no-claude"), "GH_CONFIG_DIR": str(self.home / "no-gh"),
                    "UV_OFFLINE": "1"})
        return env

    def start_viewer(self, wait: int = 180) -> None:
        port = free_port()
        log = (self.home / "product.log").open("a")
        self.viewer = subprocess.Popen(["uv", "run", "--frozen", "slope", "viewer", "--port", str(port), "--host", "127.0.0.1"],
                                       cwd=self.ws, env=self.env(), stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        self.url = f"http://127.0.0.1:{port}"
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.viewer.poll() is not None:
                raise RuntimeError(f"the product exited while starting; read {self.home / 'product.log'}")
            try:
                urllib.request.urlopen(self.url + "/", timeout=2)
                return
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.5)
        raise RuntimeError(f"the product did not answer within {wait} s; read {self.home / 'product.log'}")

    def stop_viewer(self) -> None:
        if self.viewer and self.viewer.poll() is None:
            try:
                os.killpg(self.viewer.pid, signal.SIGTERM)
                self.viewer.wait(timeout=10)
            except Exception:
                pass

    # --- what a user does --------------------------------------------------------------------------------------
    def settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=60_000)
        except Exception:
            self.errors.append("the page was still loading after 60 s")
        self.page.wait_for_timeout(300)

    def see(self, limit: int) -> dict:
        out = self.page.evaluate(SEE_JS, limit)
        out["problems"], self.errors = list(self.errors), []
        return out

    def element(self, target: str):
        if target.isdigit():
            loc = self.page.locator(f'[data-drive-ref="{target}"]')
            if loc.count() == 0:
                raise ValueError(f"No control [{target}] on the page now. Run `drive see` for the current numbers.")
            return loc.first
        loc = self.page.get_by_text(target, exact=False)
        if loc.count() == 0:
            raise ValueError(f"Nothing on the page shows the text {target!r}. Run `drive see` to read the page.")
        return loc.first

    def act(self, cmd: str, args: list[str], opts: dict) -> dict:
        limit = 0 if opts.get("all") else int(opts.get("limit", TEXT_LIMIT))
        if cmd == "status":
            return {"running": self.viewer is not None and self.viewer.poll() is None, "url": self.url,
                    "workspace": str(self.ws), "page": self.page.url}
        if cmd == "open":
            target = args[0] if args else "/"
            self.page.goto(target if target.startswith("http") else self.url + "/" + target.lstrip("/"),
                           wait_until="domcontentloaded", timeout=120_000)
        elif cmd == "see":
            pass
        elif cmd == "click":
            self.element(args[0]).click(timeout=15_000)
        elif cmd == "type":
            el = self.element(args[0])
            kind = el.evaluate("e => e.tagName.toLowerCase() === 'input' ? e.type : e.tagName.toLowerCase()")
            if kind == "select":
                try:
                    el.select_option(label=args[1], timeout=5_000)
                except Exception:
                    el.select_option(value=args[1], timeout=5_000)
            elif kind in ("range", "number", "date", "color", "time"):
                el.evaluate("(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));"
                            " e.dispatchEvent(new Event('change', {bubbles: true})); }", args[1])
            else:
                el.fill(args[1], timeout=15_000)
        elif cmd == "press":
            (self.element(args[1]).press(args[0], timeout=15_000) if len(args) > 1 else self.page.keyboard.press(args[0]))
        elif cmd == "send":
            return self.send(args, opts, limit)
        elif cmd == "run":
            return self.run(args, opts, limit)
        elif cmd == "restart":
            self.stop_viewer()
            self.start_viewer()
            return {"restarted": self.url, "from": str(self.ws), "next": "drive open /"}
        else:
            raise ValueError(f"unknown command {cmd!r}; drive --help lists them")
        self.settle()
        return self.see(limit)

    def send(self, args: list[str], opts: dict, limit: int) -> dict:
        method, path = args[0].upper(), args[1]
        body = args[2].encode() if len(args) > 2 else None
        req = urllib.request.Request(self.url + "/" + path.lstrip("/"), data=body, method=method,
                                     headers={"Content-Type": "application/json"} if body else {})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=int(opts.get("timeout", 300))) as r:
                status, text, ctype = r.status, r.read().decode(errors="replace"), r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            status, text, ctype = e.code, e.read().decode(errors="replace"), e.headers.get("content-type", "")
        if "json" in ctype and limit:
            try:
                text = json.dumps(json.loads(text), indent=1)
            except ValueError:
                pass
        if limit and len(text) > limit:
            text = text[:limit] + f"\n... ({len(text) - limit} more characters; use --all)"
        return {"status": status, "content-type": ctype, "seconds": round(time.time() - t, 2), "body": text}

    def run(self, args: list[str], opts: dict, limit: int) -> dict:
        timeout = int(opts.get("timeout", 300))
        t = time.time()
        try:
            out = subprocess.run(["uv", "run", "--frozen", "slope", *args], cwd=self.ws, env=self.env(),
                                 capture_output=True, text=True, timeout=timeout)
            code, text = out.returncode, out.stdout + out.stderr
        except subprocess.TimeoutExpired as e:
            code = "stopped"
            text = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            text += f"\n(drive stopped it after {timeout} s; pass --timeout to allow longer)"
        if limit and len(text) > limit:
            text = text[:limit] + f"\n... ({len(text) - limit} more characters; use --all)"
        return {"command": "slope " + " ".join(args), "exit": code, "seconds": round(time.time() - t, 1), "text": text}

    def close(self) -> None:
        self.stop_viewer()
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass


def main() -> int:
    ws, state_file = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    product = Product(ws, state_file)
    try:
        return serve(product, ws, state_file)
    finally:  # however the server ends, the product and the browser end with it
        product.close()
        state_file.unlink(missing_ok=True)


def serve(product: Product, ws: Path, state_file: Path) -> int:
    product.start_viewer()
    stopping = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            cmd = body.get("cmd", "")
            try:
                if cmd == "stop":
                    result, code = {"stopped": product.url}, 200
                    stopping.set()
                else:
                    result, code = product.act(cmd, body.get("args", []), body.get("opts", {})), 200
            except Exception as e:  # the message says what to do next
                result, code = {"error": str(e)}, 400
            data = json.dumps(result, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    # One request at a time, on this thread: Playwright's sync API stays on the thread that started it.
    control = HTTPServer(("127.0.0.1", 0), Handler)
    control.timeout = 5
    state_file.write_text(json.dumps({"pid": os.getpid(), "control": control.server_address[1], "url": product.url,
                                      "workspace": str(ws), "log": str(product.home / "product.log"),
                                      "started": time.time()}))
    print(json.dumps({"started": product.url, "control": control.server_address[1]}), flush=True)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stopping.set())
    while not stopping.is_set() and ws.exists():  # a server whose workspace is gone has no one left to serve
        control.handle_request()
    control.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
