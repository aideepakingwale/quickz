"""Connect a stdio-only AI app (such as Claude Desktop) to a DiQuizBuzz server's /mcp address.

Most AI apps start a local program and talk to it over standard input and output. This program does exactly that and passes
every message on to your DiQuizBuzz server over HTTPS, signed in with your personal token. It needs only Python 3.9 or newer.

    DQB_URL=https://your-server.example/mcp DQB_TOKEN=dqb_xxxxxxxx python dqb_mcp_bridge.py

Create the token under Account settings, "AI assistant access", in the host console. The token is read from the environment
so it never appears on the command line.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def post(url: str, token: str, body: bytes, timeout: float = 60) -> tuple[int, str]:
    """One JSON POST. Returns (HTTP status, reply text). Redirects are not followed, so the token cannot be sent elsewhere."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def forward(line: str, url: str, token: str, send=post, emit=print) -> None:
    """Send one message line to the server and emit its reply (nothing for notifications)."""
    line = line.strip()
    if not line:
        return
    try:
        msg = json.loads(line)
    except ValueError:
        return
    rid = msg.get("id") if isinstance(msg, dict) else None
    try:
        status, text = send(url, token, line.encode("utf-8"))
    except Exception as e:                                    # network problem: tell the app instead of going silent
        if rid is not None:
            emit(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": f"Could not reach DiQuizBuzz: {e}"}}))
        return
    if status == 202 or not text.strip():
        return
    emit(text.strip().replace("\n", " "))


def main() -> int:
    url, token = os.environ.get("DQB_URL", ""), os.environ.get("DQB_TOKEN", "")
    if not url.startswith("https://") and not url.startswith("http://localhost") and not url.startswith("http://127."):
        sys.stderr.write("Set DQB_URL to your server's https:// .../mcp address.\n")
        return 2
    if not token.startswith("dqb_"):
        sys.stderr.write("Set DQB_TOKEN to a token created under Account settings.\n")
        return 2
    for line in sys.stdin:
        forward(line, url, token, emit=lambda s: (sys.stdout.write(s + "\n"), sys.stdout.flush()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
