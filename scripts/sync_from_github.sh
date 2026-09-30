#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'Refusing sync: working tree has local changes.' >&2
  exit 1
fi
if [[ "$(git branch --show-current)" != main ]]; then
  echo 'Refusing sync: checkout must be on main.' >&2
  exit 1
fi
git fetch origin main
if [[ "$(git rev-list --count origin/main..HEAD)" != 0 ]]; then
  echo 'Refusing sync: local main contains unpushed commits.' >&2
  exit 1
fi
git merge --ff-only origin/main
python3.11 -m unittest discover -s tests -v
git rev-parse HEAD
