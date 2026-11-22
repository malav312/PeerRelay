"""Loopback-only, read-only review. Browser requests, never MCP results, carry patches."""

import html
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def render(review):
    esc = html.escape
    cards = []
    for item in review["files"]:
        badge = f"+{item['additions']} −{item['deletions']}"
        if "omitted" in item:
            body = (
                '<p class="note">Preview omitted: '
                + esc(item["omitted"].replace("_", " "))
                + ".</p>"
            )
        else:
            lines = []
            for line in item["diff"].splitlines():
                color = (
                    "add" if line.startswith("+") else "del" if line.startswith("-") else "context"
                )
                lines.append(f'<span class="{color}">{esc(line)}</span>')
            body = '<pre aria-label="File diff">' + "".join(lines) + "</pre>"
        if item.get("mode_change"):
            body = '<p class="note">Permissions: ' + esc(item["mode_change"]) + "</p>" + body
        cards.append(
            '<details><summary><span class="path">' + esc(item["path"]) + "</span>"
            '<span class="kind">'
            + esc(item["kind"])
            + '</span><span class="counts">'
            + badge
            + "</span></summary>"
            + body
            + "</details>"
        )
    counts = f"+{review['additions']} −{review['deletions']}"
    omitted = (
        f'<p class="note">Omitted file previews: {review["omitted_files"]}. '
        "Line totals cover displayed text only.</p>"
        if review["omitted_files"]
        else ""
    )
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PeerRelay · Changes</title><style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif;
background:light-dark(#f8f9fb,#17191d);
color:light-dark(#20252d,#e9edf3)}body{margin:0;padding:40px 20px}main{max-width:1000px;margin:auto}
.eyebrow,.note,.kind{color:light-dark(#626a76,#a5adba)}.eyebrow{font-size:13px;letter-spacing:.04em}
h1{font-size:26px;letter-spacing:-.03em;margin:8px 0}.note{font-size:14px;line-height:1.6}
.totals{font-family:ui-monospace,monospace;font-size:17px}.files{margin-top:28px;border:1px solid
light-dark(#dce0e7,#3a414d);border-radius:12px;overflow:hidden;background:light-dark(white,#20242b)}
details+details{border-top:1px solid light-dark(#dce0e7,#3a414d)}
summary{padding:16px;cursor:pointer;
display:flex;align-items:center;gap:12px}summary:before{content:'›';font-size:22px}details[open]
summary:before{transform:rotate(90deg)}.path{flex:1;overflow-wrap:anywhere}.kind{font-size:12px}
.counts{font:13px ui-monospace,monospace;white-space:nowrap}pre{margin:0;overflow:auto;padding:16px;
font:13px/1.6 ui-monospace,monospace;border-top:1px solid light-dark(#e8ebef,#3a414d)}
pre span{display:block;min-height:1.6em;white-space:pre}.add{background:light-dark(#e4f6e9,#173c2a)}
.del{background:light-dark(#ffe7e6,#472322)}details .note{padding:0 16px}
summary:focus-visible{outline:2px solid #528bff;outline-offset:-3px}
@media(max-width:550px){body{padding:24px 12px}.kind{display:none}summary{padding:12px}}
</style><main><div class="eyebrow">PEERRELAY · READ-ONLY REVIEW</div><h1>"""
        + (
            f"{review['files_changed']} changed file"
            + ("" if review["files_changed"] == 1 else "s")
        )
        + '</h1><div class="totals">'
        + counts
        + "</div>"
        + omitted
        + """
<p class="note">Changes between task start and completion in the registered project.
Existing edits are the baseline; edits made by other tools during that interval may appear.
Dependency and cache directories are excluded.</p>
<div class="files">"""
        + ("".join(cards) or '<p class="note">No changes to review.</p>')
        + "</div></main></html>"
    )


class ChangesViewer:
    def __init__(self, client, clock=time.monotonic):
        self.client, self.clock = client, clock
        self.lock = threading.Lock()
        self.server = self.thread = None
        self.links = {}
        self.closed = False

    def link(self, mode, task):
        with self.lock:
            if self.closed:
                raise ValueError("Viewer is closed")
            if self.server is None:
                viewer = self

                class Handler(BaseHTTPRequestHandler):
                    def setup(self):
                        super().setup()
                        self.connection.settimeout(5)

                    def log_message(self, *args):
                        pass  # URLs contain local capabilities; never log them.

                    def do_GET(self):
                        origin = f"http://127.0.0.1:{self.server.server_port}"
                        if self.headers.get("Host") != origin.removeprefix("http://") or (
                            self.headers.get("Origin") not in (None, origin)
                        ):
                            self.reply(403, "Review unavailable")
                            return
                        with viewer.lock:
                            binding = viewer.links.get(self.path)
                        if not binding or binding[2] <= viewer.clock():
                            self.reply(404, "Review unavailable")
                            return
                        try:
                            review = viewer.client.call(
                                "changes",
                                {
                                    "mode_id": binding[0],
                                    "task_id": binding[1],
                                },
                            )
                            if review["state"] != "available":
                                raise ValueError("Unavailable")
                            page = render(review)
                        except Exception:
                            self.reply(404, "Review unavailable")
                            return
                        self.reply(200, page)

                    def reply(self, status, page):
                        # POSIX paths may contain surrogateescaped bytes. Display
                        # those as literal escapes; keep stored path identities intact.
                        data = page.encode("utf-8", errors="backslashreplace")
                        self.send_response(status)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Referrer-Policy", "no-referrer")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header(
                            "Content-Security-Policy",
                            "default-src 'none'; "
                            "style-src 'unsafe-inline'; frame-ancestors 'none'; "
                            "base-uri 'none'; form-action 'none'",
                        )
                        self.end_headers()
                        self.wfile.write(data)

                self.server = HTTPServer(("127.0.0.1", 0), Handler)
                self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
                self.thread.start()
            self.links = {
                key: value for key, value in self.links.items() if value[2] > self.clock()
            }
            path = next(
                (key for key, value in self.links.items() if value[:2] == (mode, task)), None
            )
            if path is None:
                # Bound the session's capabilities and discard the oldest link.
                if len(self.links) >= 64:
                    self.links.pop(next(iter(self.links)))
                path = "/review/" + secrets.token_urlsafe(32)
                self.links[path] = (mode, task, self.clock() + 3600)
            return f"http://127.0.0.1:{self.server.server_port}{path}"

    def close(self):
        with self.lock:
            self.closed = True
            self.links.clear()
            server, thread = self.server, self.thread
            self.server = self.thread = None
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=11)
