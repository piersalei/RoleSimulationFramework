import argparse
import datetime
import json
import sys
from pathlib import Path

from .catalog import build_catalog, write_json
from .persona import compile_views, validate_persona
from .providers import ChatBackend, MockBackend, strict_json
from .agent import MODES
from .audit import write_report
from .package import export_package
from .runtime import run_episode
from .skills import bind, load_library


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
    audit = sub.add_parser("audit", help="re-audit a finished run directory")
    audit.add_argument("run_dir")
    package = sub.add_parser("package", help="export a blinded review package from finished runs")
    package.add_argument("run_dirs", nargs="+")
    package.add_argument("--out", required=True)
    package.add_argument("--seed", type=int, default=0)
    skills = sub.add_parser("skills", help="validate the skill library and, optionally, a persona binding")
    skills.add_argument("binding", nargs="?")
    skills.add_argument("--persona")
    demo = sub.add_parser("run", help="one focal role in a scripted environment")
    demo.add_argument("--persona", default="examples/personas/cautious.json")
    demo.add_argument("--skills", default=None, help="persona skills binding, e.g. examples/personas/cautious.skills.json")
    demo.add_argument("--no-skills", action="store_true", help="record the binding but keep skills out of the prompts")
    demo.add_argument("--scenario", default="examples/scenes/cooperation_conflict.json")
    demo.add_argument("--backend", choices=["mock", "chat"], default="mock")
    demo.add_argument("--mode", choices=list(MODES), default="profile_state_mechanisms")
    demo.add_argument("--out", default=None)
    args = parser.parse_args()
    try:
        if args.command == "skills":
            library = load_library()
            print(f"Library: {len(library)} skills ({', '.join(library)})")
            if args.binding:
                if not args.persona:
                    raise ValueError("--persona is required to check a binding")
                persona = strict_json(Path(args.persona).read_text(encoding="utf-8"))
                validate_persona(persona)
                bound = bind(strict_json(Path(args.binding).read_text(encoding="utf-8")), persona["id"], library)
                print(f"VALID binding for {persona['id']}: {[s['name'] for s in bound]}")
            return
        if args.command == "catalog":
            data = build_catalog(args.sources)
            write_json(args.out, data)
            print(f"Indexed {len(data['sections'])} reference sections; no theory was automatically enabled.")
            return
        if args.command == "audit":
            report = write_report(args.run_dir)
            print(f"{'PASS' if report['passed'] else 'FAIL'}: {args.run_dir}/audit_report.md")
            sys.exit(0 if report["passed"] else 2)
        if args.command == "package":
            print(json.dumps(export_package(args.run_dirs, args.out, args.seed), ensure_ascii=False))
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
            binding = strict_json(Path(args.skills).read_text(encoding="utf-8")) if args.skills else None
            if args.no_skills and binding is None:
                raise ValueError("--no-skills needs --skills: the control run records the binding it leaves out")
            backend = MockBackend() if args.backend == "mock" else ChatBackend()
            output = args.out or "runs/" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            result = run_episode(persona, scenario, backend, output, args.mode, binding, not args.no_skills)
            print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
