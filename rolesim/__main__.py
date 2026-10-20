import argparse
import datetime
import json
import sys

from .catalog import build_catalog, write_json
from .persona import compile_views
from .providers import ChatBackend, MockBackend
from .agent import MODES
from .audit import write_report
from .package import export_package
from .roles import ROLES_DIR, SCENES_DIR, available, fit_cast, load_role, load_scene, run
from .skills import load_library

DEFAULT_ROLES = ["role1", "role2"]
DEFAULT_SCENE = "test1"


def main():
    parser = argparse.ArgumentParser(description="RoleSimulationFramework research prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog = sub.add_parser("catalog", help="index source documents as reference data")
    catalog.add_argument("--sources", default="data/sources")
    catalog.add_argument("--out", default="data/catalog.json")
    listing = sub.add_parser("list", help="list roles and scenes, or check one cast in one scene")
    listing.add_argument("--roles", nargs="+", help="one role id or path per seat, in seat order")
    listing.add_argument("--scene", help="scene id (scenes/<id>.json) or path")
    for name in ("validate", "compile"):
        item = sub.add_parser(name, help=f"{name} a role file")
        item.add_argument("role", help="role id or path")
        if name == "compile":
            item.add_argument("--out", required=True)
    skills = sub.add_parser("skills", help="validate the skill library and, optionally, a role's skills")
    skills.add_argument("role", nargs="?")
    audit = sub.add_parser("audit", help="re-audit a finished run directory")
    audit.add_argument("run_dir")
    package = sub.add_parser("package", help="export a blinded review package from finished runs")
    package.add_argument("run_dirs", nargs="+")
    package.add_argument("--out", required=True)
    package.add_argument("--seed", type=int, default=0)
    demo = sub.add_parser("run", help="two roles talking in a background-only scene")
    demo.add_argument("--roles", nargs="+", default=DEFAULT_ROLES,
                      help="one role id (roles/<id>.json) or path per seat, in seat order")
    demo.add_argument("--scene", default=DEFAULT_SCENE, help="scene id (scenes/<id>.json) or path")
    demo.add_argument("--no-skills", action="store_true", help="keep the roles' skills on record but out of the prompts")
    demo.add_argument("--backend", choices=["mock", "chat"], default="mock")
    demo.add_argument("--mode", choices=list(MODES), default="profile_state_mechanisms")
    demo.add_argument("--out", default=None)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            data = build_catalog(args.sources)
            write_json(args.out, data)
            print(f"Indexed {len(data['sections'])} reference sections; no theory was automatically enabled.")
        elif args.command == "list":
            _list(args)
        elif args.command == "validate":
            role = load_role(args.role)
            print(f"VALID: {role['id']} ({role['persona']['name']})")
        elif args.command == "compile":
            write_json(args.out, compile_views(load_role(args.role)["persona"]))
            print(args.out)
        elif args.command == "skills":
            library = load_library()
            print(f"Library: {len(library)} skills ({', '.join(library)})")
            if args.role:
                role = load_role(args.role, library=library)
                print(f"VALID skills for {role['id']}: {[s['name'] for s in role['bound'] or []]}")
        elif args.command == "audit":
            report = write_report(args.run_dir)
            print(f"{'PASS' if report['passed'] else 'FAIL'}: {args.run_dir}/audit_report.md")
            sys.exit(0 if report["passed"] else 2)
        elif args.command == "package":
            print(json.dumps(export_package(args.run_dirs, args.out, args.seed), ensure_ascii=False))
        else:
            roles, scene = [load_role(r) for r in args.roles], load_scene(args.scene)
            backend = MockBackend() if args.backend == "mock" else ChatBackend()
            output = args.out or "runs/" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            result = run(roles, scene, backend, output, args.mode, not args.no_skills)
            print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _list(args):
    if bool(args.roles) != bool(args.scene):
        raise ValueError("give both --roles and --scene, or neither to list everything")
    if args.roles:
        roles, scene = [load_role(r) for r in args.roles], load_scene(args.scene)
        result = fit_cast(roles, scene)
        print(f"{'FITS' if result['ok'] else 'DOES NOT FIT'}: {' + '.join(r['id'] for r in roles)} -> {scene['id']}")
        for line in result["errors"]:
            print(f"  error: {line}")
        for line in result["notes"]:
            print(f"  note: {line}")
        sys.exit(0 if result["ok"] else 2)
    library = load_library()
    roles = {i: load_role(i, library=library) for i in available(ROLES_DIR)}
    print("Roles:  " + ", ".join(f"{i} ({r['persona']['name']})" for i, r in roles.items()))
    print("Scenes:")
    for i in available(SCENES_DIR):
        s = load_scene(i)["scenario"]
        seats = " / ".join(seat["title"] for seat in s["seats"])
        print(f"  {i}: seats {seats}; up to {s['max_turns']} turns")


if __name__ == "__main__":
    main()
