#!/usr/bin/env python3
"""ask-form: show Claude's clarifying questions as one browser form and wait for the answers.

Usage: ask_form.py QUESTIONS.json [--out ANSWERS.json] [--timeout SECONDS] [--no-open] [--quiet]

Starts a server on 127.0.0.1 (random port), opens the browser, blocks until the
form is submitted, writes the answers JSON and prints a readable summary.
Exit codes: 0 submitted, 2 timed out / interrupted, 1 bad input.
Standard library only; works on Windows, Linux, macOS and WSL.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "form.html"
# outside the skill folder, so it survives plugin updates
PREFS = Path.home() / ".claude" / "ask-form" / "prefs.json"
TYPES = ("single", "multi", "text", "rank", "scale")

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def fail(msg):
    print(f"ask-form: {msg}", file=sys.stderr)
    sys.exit(1)


def load_questions(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        fail(f"cannot read {path}: {e}")
    if isinstance(data, list):
        data = {"questions": data}
    qs = data.get("questions")
    if not isinstance(qs, list) or not qs:
        fail("'questions' must be a non-empty list")
    seen = set()
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict) or not str(q.get("question", "")).strip():
            fail(f"question #{i} needs a 'question' text")
        q.setdefault("id", f"q{i}")
        q["id"] = str(q["id"])
        if q["id"] in seen:
            fail(f"duplicate id '{q['id']}'")
        seen.add(q["id"])
        q.setdefault("type", "single" if q.get("options") else "text")
        if q["type"] not in TYPES:
            fail(f"question '{q['id']}': type must be one of {TYPES}")
        if q["type"] in ("single", "multi", "rank"):
            opts = q.get("options") or []
            if not opts:
                fail(f"question '{q['id']}': {q['type']} needs 'options'")
            q["options"] = [o if isinstance(o, dict) else {"label": str(o)} for o in opts]
            q.setdefault("allowOther", q["type"] != "rank")
            if q["type"] == "rank":
                q["allowOther"] = False
        if q["type"] == "scale":
            q.setdefault("min", 1)
            q.setdefault("max", 5)
            if not (isinstance(q["min"], int) and isinstance(q["max"], int) and 1 <= q["max"] - q["min"] <= 10):
                fail(f"question '{q['id']}': scale needs integer min < max, at most 11 steps")
    for q in qs:
        cond = q.get("showIf")
        if cond is not None:
            if not isinstance(cond, dict) or cond.get("question") not in seen or cond.get("question") == q["id"]:
                fail(f"question '{q['id']}': showIf needs {{\"question\": <id of another question>, \"anyOf\": [...]}}")
            if not isinstance(cond.get("anyOf"), list):
                cond["anyOf"] = [cond.get("anyOf")]
    data.setdefault("title", "Questions from Claude")
    return data


def read_prefs():
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_prefs(prefs):
    try:
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        PREFS.write_text(json.dumps(prefs), encoding="utf-8")
    except OSError:
        pass


def is_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_browser(url):
    if is_wsl():
        # A server bound to 127.0.0.1 in WSL2 is reachable from Windows via localhost forwarding.
        cmd_exe = shutil.which("cmd.exe") or "/mnt/c/Windows/System32/cmd.exe"
        for cmd in (["wslview", url], [cmd_exe, "/c", "start", "", url]):
            if not (shutil.which(cmd[0]) or os.path.exists(cmd[0])):
                continue
            try:
                subprocess.Popen(cmd, cwd="/mnt/c" if os.path.isdir("/mnt/c") else None,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
        return False
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def chime():
    """Play a short system sound so the user notices the form opened. Never fails."""
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif is_wsl():
            ps = shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            subprocess.Popen([ps, "-NoProfile", "-Command", "[System.Media.SystemSounds]::Asterisk.Play(); Start-Sleep -Milliseconds 600"],
                             cwd="/mnt/c" if os.path.isdir("/mnt/c") else None,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            with open("/dev/tty", "w") as tty:
                tty.write("\a")
    except Exception:
        pass


def is_visible(q, by_id, answers, seen=None):
    """Mirror of the page's showIf logic."""
    cond = q.get("showIf")
    if not cond:
        return True
    seen = seen or set()
    ref = by_id.get(cond["question"])
    if ref is None or q["id"] in seen:
        return True
    seen.add(q["id"])
    if not is_visible(ref, by_id, answers, seen):
        return False
    a = answers.get(ref["id"]) or {}
    if a.get("delegated"):
        return False
    picked = list(a.get("selected") or [])
    if a.get("other"):
        picked.append("__other")
    if a.get("value") is not None:
        picked.append(a["value"])
    return any(v in picked for v in cond["anyOf"])


def is_answered(q, a):
    if q.get("optional"):
        return True
    if a.get("delegated"):
        return True
    if q["type"] == "text":
        return bool(a.get("text"))
    if q["type"] == "rank":
        return bool(a.get("ranking"))
    if q["type"] == "scale":
        return a.get("value") is not None
    other = a.get("other")
    if other is not None and not other:
        return False
    return bool(a.get("selected")) or bool(other)


def build_result(data, raw):
    out, missing = [], []
    by_id = {q["id"]: q for q in data["questions"]}
    for q in data["questions"]:
        a = raw.get(q["id"]) or {}
        item = {"id": q["id"], "question": q["question"], "type": q["type"],
                "delegated": bool(a.get("delegated")), "note": (a.get("note") or "").strip()}
        if q["type"] == "text":
            item["text"] = (a.get("text") or "").strip()
        elif q["type"] == "rank":
            labels = [o["label"] for o in q["options"]]
            ranking = [x for x in a.get("ranking") or [] if x in labels]
            item["ranking"] = list(dict.fromkeys(ranking)) + [x for x in labels if x not in ranking]
        elif q["type"] == "scale":
            v = a.get("value")
            item["value"] = v if isinstance(v, int) and q["min"] <= v <= q["max"] else None
            item["scale"] = {k: q.get(k) for k in ("min", "max", "minLabel", "maxLabel") if q.get(k) is not None}
        else:
            labels = {o["label"] for o in q["options"]}
            item["selected"] = [s for s in a.get("selected") or [] if s in labels]
            other = a.get("other")
            item["other"] = other.strip() if isinstance(other, str) else None
        if not is_visible(q, by_id, raw):
            item["hidden"] = True
            out.append(item)
            continue
        if not is_answered(q, item):
            missing.append(q["id"])
        if q.get("optional"):
            item["optional"] = True
            item["skipped"] = not is_answered(dict(q, optional=False), item)
        out.append(item)
    return out, missing


def summary(result, out_path):
    lines = [f"ANSWERS SUBMITTED (saved to {out_path})", ""]
    for i, a in enumerate(result, 1):
        lines.append(f"{i}. [{a['id']}] {a['question']}")
        if a.get("hidden"):
            lines.append("   -> (not shown: its showIf condition was not met)")
        elif a["delegated"]:
            lines.append("   -> (the user leaves this decision to you)")
        elif a.get("skipped"):
            lines.append("   -> (optional, left empty)")
        elif a["type"] == "text":
            lines += ["   -> " + ln for ln in a["text"].splitlines()]
        elif a["type"] == "rank":
            lines += [f"   {n}. {label}" for n, label in enumerate(a["ranking"], 1)]
        elif a["type"] == "scale":
            sc = a["scale"]
            ends = ", ".join(f"{sc[k[:3]]} = {sc[k]}" for k in ("minLabel", "maxLabel") if k in sc)
            lines.append(f"   -> {a['value']} on {sc['min']}-{sc['max']}" + (f" ({ends})" if ends else ""))
        else:
            if a["selected"]:
                lines.append("   -> " + "; ".join(a["selected"]))
            if a["other"]:
                lines.append("   -> Other: " + a["other"])
        if a["note"]:
            lines.append("   Note: " + a["note"].replace("\n", " / "))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions")
    ap.add_argument("--out", help="answers file (default: <questions>.answers.json)")
    ap.add_argument("--timeout", type=int, default=7200, help="seconds to wait (default 7200)")
    ap.add_argument("--no-open", action="store_true", help="only print the URL")
    ap.add_argument("--quiet", action="store_true", help="no sound when the form opens")
    ap.add_argument("--project", help="label shown in the header (default: name of the current directory)")
    args = ap.parse_args()

    data = load_questions(args.questions)
    # tells concurrent forms from different Claude sessions apart
    data["project"] = args.project or data.get("project") or Path.cwd().name
    qpath = Path(args.questions).resolve()
    out_path = Path(args.out).resolve() if args.out else qpath.with_name(qpath.stem + ".answers.json")
    token = secrets.token_urlsafe(16)
    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except OSError as e:
        fail(f"missing template: {e}")

    def page():
        payload = dict(data, token=token, prefs=read_prefs())
        blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return template.replace("/*__DATA__*/null", blob)

    state = {"result": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self, url):
            return parse_qs(url.query).get("t", [""])[0] == token

        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/":
                return self.send(404, "not found", "text/plain")
            if not self.authorized(url):
                return self.send(403, "Invalid link", "text/plain")
            self.send(200, page(), "text/html")

        def do_POST(self):
            url = urlparse(self.path)
            if not self.authorized(url):
                return self.send(403, '{"error":"forbidden"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self.send(400, '{"error":"bad json"}')
            if url.path == "/pref":
                prefs = read_prefs()
                prefs.update({k: v for k, v in body.items() if k == "theme"})
                write_prefs(prefs)
                return self.send(200, "{}")
            if url.path != "/submit":
                return self.send(404, '{"error":"not found"}')
            if state["result"] is not None:
                return self.send(200, '{"ok":true}')
            result, missing = build_result(data, body.get("answers") or {})
            if missing:
                return self.send(400, json.dumps({"error": "unanswered", "missing": missing}))
            doc = {"title": data["title"], "submittedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "answers": result}
            out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            state["result"] = result
            self.send(200, '{"ok":true}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://localhost:{server.server_address[1]}/?t={token}"
    timer = threading.Timer(args.timeout, server.shutdown)
    timer.daemon = True
    timer.start()

    print(f"ask-form: {len(data['questions'])} questions waiting at {url}", flush=True)
    if args.no_open or not open_browser(url):
        print("ask-form: open the URL above in your browser.", flush=True)
    if not args.quiet:
        chime()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()

    if state["result"] is None:
        print("ask-form: no answers (timed out or interrupted).", flush=True)
        sys.exit(2)
    print(summary(state["result"], out_path), flush=True)


if __name__ == "__main__":
    main()
