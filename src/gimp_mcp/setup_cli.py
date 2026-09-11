from __future__ import annotations

import argparse
import json
from pathlib import Path

from .setup_manager import SetupManager


def main() -> int:
    parser=argparse.ArgumentParser(prog="gimp-mcp-setup")
    parser.add_argument("command", choices=("status", "gimp", "mcp-update"))
    args=parser.parse_args()
    manager=SetupManager(Path(__file__).resolve().parents[2])
    if args.command == "gimp":
        result=manager.managed_gimp_status()
    elif args.command == "mcp-update":
        result=manager.gimp_mcp_update_status()
    else:
        result={"gimp":manager.managed_gimp_status(), "gimp_mcp":manager.gimp_mcp_update_status()}
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
