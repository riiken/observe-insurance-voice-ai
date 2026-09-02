#!/usr/bin/env python
"""Create (or update) the Vapi assistant from this backend's own configuration.

Reads `/api/v1/voice/assistant-config` from a running instance, so the prompt
and the five tool schemas come from the code that implements them — nothing is
retyped, and nothing can drift.

Prints the payload by default. Pass --create to actually send it.

    export VAPI_API_KEY=...                       # dashboard -> Organization -> API Keys (private)
    export BASE_URL=https://<your>.trycloudflare.com
    python scripts/create_vapi_assistant.py                    # dry run: show the payload
    python scripts/create_vapi_assistant.py --create           # create it
    python scripts/create_vapi_assistant.py --update <id>      # update an existing one
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

VAPI_API = "https://api.vapi.ai/assistant"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_env_secret() -> str | None:
    """The webhook secret from .env, so it is never typed on a command line."""
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "VOICE_PLATFORM_API_KEY" and value.strip():
            return value.strip()
    return None


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def build_payload(base_url: str, secret: str) -> dict:
    """Turn our assistant-config into a Vapi assistant object."""
    config = _get_json(f"{base_url}/api/v1/voice/assistant-config")
    model = config["model"]
    webhook = f"{base_url}/api/v1/voice/webhook"

    # Every tool posts to the same webhook, with the same shared secret.
    tools = []
    for tool in model["tools"]:
        tools.append(
            {
                "type": "function",
                "function": tool["function"],
                "server": {"url": webhook, "secret": secret},
                "async": False,
            }
        )

    return {
        "name": "Observe Insurance Claims",
        "firstMessage": config["firstMessage"],
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "anthropic",
            "model": model["model"],
            "temperature": model["temperature"],
            "messages": [
                {"role": "system", "content": model["messages"][0]["content"]}
            ],
            "tools": tools,
        },
        "server": {"url": webhook, "secret": secret},
        # Exactly the three the backend acts on. Transcripts and speech updates
        # are noise: reacting to a partial transcript means reacting to half a
        # sentence.
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
    }


def send(payload: dict, api_key: str, assistant_id: str | None) -> None:
    url = f"{VAPI_API}/{assistant_id}" if assistant_id else VAPI_API
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="PATCH" if assistant_id else "POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        print(f"Vapi returned {exc.code}:\n{exc.read().decode()}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"assistant id : {body.get('id')}")
    print(f"name         : {body.get('name')}")
    print(f"tools        : {len(body.get('model', {}).get('tools', []))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="actually create it")
    parser.add_argument(
        "--update", metavar="ASSISTANT_ID", help="update an existing assistant"
    )
    args = parser.parse_args()

    base_url = os.environ.get("BASE_URL", "").rstrip("/")
    if not base_url:
        raise SystemExit("Set BASE_URL to your public tunnel URL.")

    secret = _read_env_secret()
    if not secret:
        raise SystemExit("VOICE_PLATFORM_API_KEY is not set in .env.")

    payload = build_payload(base_url, secret)

    if not (args.create or args.update):
        # Mask the secret in the printed payload; it is still sent in full.
        shown = json.loads(json.dumps(payload))
        shown["server"]["secret"] = "***"
        for tool in shown["model"]["tools"]:
            tool["server"]["secret"] = "***"
        print(json.dumps(shown, indent=2))
        print("\nDry run. Re-run with --create to send this to Vapi.", file=sys.stderr)
        return

    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key:
        raise SystemExit(
            "Set VAPI_API_KEY (dashboard -> Organization -> API Keys, private key)."
        )

    send(payload, api_key, args.update)


if __name__ == "__main__":
    main()
