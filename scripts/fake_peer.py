"""Fake peer — drives real-time chat updates against a running backend.

Run alongside a Flutter app during development. Logs in as an existing user
and posts a new message every N seconds into a conversation, so the dev
can watch the Flutter screen update without manually typing on a second
emulator. If messages don't appear on the Flutter screen, the WebSocket
integration is broken (see docs/ui-contract/websocket-integration.md §6).

Usage:
    python scripts/fake_peer.py \\
        --base https://<your-ngrok>.ngrok-free.dev \\
        --conversation-id <cid> \\
        --email user1@demo.school \\
        --password DemoPass123! \\
        --interval 5

The peer sends via REST (POST /messages). The server broadcasts over
WebSocket to every subscriber — exactly the flow the Flutter app
must observe.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
) -> tuple[int, dict]:
    """Make a JSON HTTP request. Returns (status_code, parsed_body)."""
    headers = {
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except Exception:
            payload = {"error": {"message": str(exc)}}
        return exc.code, payload


def login(base: str, email: str, password: str) -> str:
    code, body = _request(
        "POST",
        f"{base}/api/v1/auth/login",
        body={"email": email, "password": password},
    )
    if code != 200:
        print(f"login failed ({code}): {body}", file=sys.stderr)
        sys.exit(2)
    return body["data"]["access_token"]


def send_message(base: str, token: str, conversation_id: str, content: str) -> bool:
    code, body = _request(
        "POST",
        f"{base}/api/v1/chat/conversations/{conversation_id}/messages",
        token=token,
        body={"content": content},
    )
    if code >= 300:
        print(f"  ! send failed ({code}): {body}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--base", required=True, help="Backend base URL (https://...)")
    parser.add_argument("--email", required=True, help="Login email")
    parser.add_argument("--password", required=True, help="Login password")
    parser.add_argument("--conversation-id", required=True, help="Conversation UUID")
    parser.add_argument(
        "--interval", type=float, default=5.0, help="Seconds between messages"
    )
    parser.add_argument(
        "--count", type=int, default=0, help="Stop after N messages (0 = forever)"
    )
    parser.add_argument(
        "--prefix", default="peer", help="Message prefix (shown in message text)"
    )
    args = parser.parse_args()

    base = args.base.rstrip("/")
    print(f"--> logging in as {args.email} against {base}")
    token = login(base, args.email, args.password)
    print(f"--> logged in. pushing a message every {args.interval}s.")
    print(
        "    watch the Flutter chat screen — messages must appear without tapping "
        "anything. Ctrl-C to stop.\n"
    )

    sent = 0
    try:
        while True:
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            content = f"{args.prefix} @ {now} (#{sent + 1})"
            ok = send_message(base, token, args.conversation_id, content)
            if ok:
                print(f"  [{now}] sent: {content}")
                sent += 1
            if args.count and sent >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n--> stopped. total messages sent:", sent)
        return 0

    print("\n--> done. total messages sent:", sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
