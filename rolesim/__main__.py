import argparse
import json
import sys
from pathlib import Path

from .offline.audit import write_report
from .catalog import build_catalog, write_json
from .interfaces import FULL_MODE, MODES
from .offline.package import export_package
from .offline.readable import FILENAME, write_readable
from .role.registry import build_registry
from .role.roles import ROLES_DIR, SCENES_DIR, available, fit_cast, load_role, load_scene
from .run.scene import environment_agent, role_seats, run_name
from .role.skills import load_library

DEFAULT_ROLES = ["role1", "role2"]
DEFAULT_SCENE = "test1"
RUNS_DIR = Path("runs")


def main():
    parser = argparse.ArgumentParser(description="RoleSimulationFramework research prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog = sub.add_parser("catalog", help="index source documents as reference data")
    catalog.add_argument("--sources", default="data/sources")
    catalog.add_argument("--out", default="data/catalog.json")
    registry = sub.add_parser("registry", help="rebuild the psychology registry (IRegistry data) from the v1 sources")
    registry.add_argument("--sources", default="data/sources/psychology_v1")
    registry.add_argument("--out", default="data/registry.json")
    prof = sub.add_parser("profile", help="compile a role's profile (profiles/<id>.json, else its role file) and list what needs review")
    prof.add_argument("role")
    prof.add_argument("--export", help="write the profile-1 source (a starting point for profiles/<id>.json) to this path")
    prof.add_argument("--backend", choices=["mock", "chat"], default="mock", help="model that reads natural-language profile text")
    listing = sub.add_parser("list", help="list roles and scenes, or check one cast in one scene")
    listing.add_argument("--roles", nargs="+", help="one role id or path per seat, in seat order")
    listing.add_argument("--scene", help="scene id (scenes/<id>.json) or path")
    validate = sub.add_parser("validate", help="validate a role file")
    validate.add_argument("role", help="role id or path")
    skills = sub.add_parser("skills", help="validate the skill library and, optionally, a role's skills")
    skills.add_argument("role", nargs="?")
    audit = sub.add_parser("audit", help="re-audit a finished run directory")
    audit.add_argument("run_dir")
    package = sub.add_parser("package", help="export a blinded review package from finished runs")
    package.add_argument("run_dirs", nargs="+")
    package.add_argument("--out", required=True)
    package.add_argument("--seed", type=int, default=0)
    demo = sub.add_parser("run", help="two to six roles in a background-only scene, behind the agent interface v2")
    demo.add_argument("--roles", nargs="+", default=DEFAULT_ROLES,
                      help="one role id (roles/<id>.json) or path per seat, in seat order")
    demo.add_argument("--scene", default=DEFAULT_SCENE, help="scene id (scenes/<id>.json) or path")
    demo.add_argument("--no-skills", action="store_true", help="keep the roles' skills on record but out of the prompts")
    demo.add_argument("--backend", choices=["mock", "chat"], default="mock")
    demo.add_argument("--mode", choices=list(MODES), default=FULL_MODE,
                      help="ablation: profile_only / profile_state / profile_state_mechanisms (full)")
    demo.add_argument("--out", default=None,
                      help="run directory; default runs/<YYYYMMDD-HHMMSS>_<scene>_<model>")
    readable = sub.add_parser("readable", help="(re)write transcript.md for finished run directories")
    readable.add_argument("run_dirs", nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            data = build_catalog(args.sources)
            write_json(args.out, data)
            print(f"Indexed {len(data['sections'])} reference sections; no theory was automatically enabled.")
        elif args.command == "registry":
            data = build_registry(args.sources)
            write_json(args.out, data)
            counts = ", ".join(f"{v} {k}" for k, v in data["counts"].items())
            print(f"Registry: {counts} -> {args.out}")
            for item in data["review"]:
                print(f"  review: {len(item['items'])} {item['topic']}")
        elif args.command == "profile":
            _profile(args)
        elif args.command == "list":
            _list(args)
        elif args.command == "validate":
            role = load_role(args.role)
            print(f"VALID: {role['id']} ({role['persona']['name']})")
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
        elif args.command == "readable":
            for run_dir in args.run_dirs:
                print(write_readable(run_dir))
        else:
            _run(args)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _run(args):
    from .run.loop import run_scene
    from .stage.model import ChatModel, MockModel
    from .role.registry import Registry
    registry = Registry.load()
    library = load_library()
    roles, scene = [load_role(r, library=library) for r in args.roles], load_scene(args.scene)
    model = MockModel(registry) if args.backend == "mock" else ChatModel()
    output = args.out or str(RUNS_DIR / run_name(scene["id"], "mock" if args.backend == "mock" else model.model))
    print(f"run directory: {output}", file=sys.stderr, flush=True)
    result = run_scene(roles, scene, model, output, registry, args.mode, not args.no_skills)
    print(json.dumps({"output": output, "transcript": str(Path(output) / FILENAME), **result}, ensure_ascii=False, indent=2))


def _profile(args):
    from collections import Counter
    from .stage.model import ChatModel, MockModel
    from .role.profile import ProfileCompiler
    from .role.registry import Registry
    from .role.repository import ProfileRepository
    registry = Registry.load()
    library = load_library()
    roles = [load_role(i, library=library) for i in available(ROLES_DIR)]
    model = MockModel(registry) if args.backend == "mock" else ChatModel()
    repo = ProfileRepository(registry, ProfileCompiler(registry, model), roles)
    profile = repo.load(args.role)
    counts = Counter((m.status, m.source_kind) for m in profile.modules.values())
    print(f"Profile {args.role}: snapshot {profile.snapshot_id[:12]}; "
          + ", ".join(f"{n} {status}/{kind}" for (status, kind), n in sorted(counts.items())))
    for item in repo.review[args.role]:
        print(f"  review: {item.construct_id}: {item.reason}")
    if args.export:
        write_json(args.export, repo.raw(args.role))
        print(args.export)


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
        seats = " / ".join(seat["title"] for seat in role_seats(s))
        world = environment_agent(s)
        extra = f"; environment {world['title']}" if world else ""
        print(f"  {i}: seats {seats}{extra}; up to {s['max_turns']} turns")


if __name__ == "__main__":
    main()
