import argparse
import datetime
import json
import sys
from pathlib import Path

from .catalog import build_catalog, write_json
from .persona import compile_views, validate_persona
from .providers import ChatBackend, MockBackend, strict_json
from .runtime import run_episode


def main():
    parser = argparse.ArgumentParser(description="RoleSimulationFramework research prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog = sub.add_parser("catalog", help="index source documents as reference data")
    catalog.add_argument("--sources", default="data/sources")
    catalog.add_argument("--out", default="data/catalog.json")
    for name in ("validate", "compile"):
        item = sub.add_parser(name)
        item.add_argument("persona")
        if name == "compile":
            item.add_argument("--out", required=True)
    demo = sub.add_parser("run", help="one focal role in a scripted environment")
    demo.add_argument("--persona", default="examples/cautious.json")
    demo.add_argument("--scenario", default="examples/cooperation_conflict.json")
    demo.add_argument("--backend", choices=["mock", "chat"], default="mock")
    demo.add_argument("--mode", choices=["mechanisms", "profile_only"], default="mechanisms")
    demo.add_argument("--out", default=None)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            data = build_catalog(args.sources)
            write_json(args.out, data)
            print(f"Indexed {len(data['sections'])} reference sections; no theory was automatically enabled.")
            return
        persona = strict_json(Path(args.persona).read_text(encoding="utf-8"))
        if args.command == "validate":
            validate_persona(persona)
            print(f"VALID: {persona['id']}")
        elif args.command == "compile":
            write_json(args.out, compile_views(persona))
            print(args.out)
        else:
            scenario = strict_json(Path(args.scenario).read_text(encoding="utf-8"))
            backend = MockBackend() if args.backend == "mock" else ChatBackend()
            output = args.out or "runs/" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            result = run_episode(persona, scenario, backend, output, args.mode)
            print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
