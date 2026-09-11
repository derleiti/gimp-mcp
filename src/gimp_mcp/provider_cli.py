from __future__ import annotations

import argparse
import json

from .provider_manager import ProviderManager


def main() -> int:
    parser = argparse.ArgumentParser(prog="gimp-mcp-provider")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "models", "connect"):
        p = sub.add_parser(name)
        p.add_argument("provider", choices=("chatgpt", "claude", "gemini", "mistral", "triforce"))
    args = parser.parse_args()
    manager = ProviderManager()
    if args.command == "status":
        status = manager.provider_status(args.provider)
        models = manager.models(args.provider) if status.get("authenticated") else []
        payload = {"status": status, "models": models}
    elif args.command == "models":
        payload = manager.models(args.provider)
    else:
        payload = manager.connect(args.provider, open_browser=True)
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
