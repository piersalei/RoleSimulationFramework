#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
git rev-parse --verify HEAD >/dev/null
if [[ "$(git branch --show-current)" != main || -n "$(git status --porcelain)" ]]; then
  echo 'Requires main with a committed, clean working tree.' >&2
  exit 1
fi
gh auth status
repo_owner="$(gh api user --jq .login)"
repo_name="$repo_owner/RoleSimulationFramework"
if git remote get-url origin >/dev/null 2>&1; then
  origin_url="$(git remote get-url origin)"
  case "$origin_url" in
    "https://github.com/$repo_name.git"|"https://github.com/$repo_name"|"git@github.com:$repo_name.git") ;;
    *) echo 'Refusing to push: existing origin does not match the intended repository.' >&2; exit 1 ;;
  esac
  if [[ "$(gh repo view "$repo_name" --json isPrivate --jq .isPrivate)" != true ]]; then
    echo 'Refusing to push: repository must be private.' >&2
    exit 1
  fi
  git push -u origin main
else
  gh repo create "$repo_name" --private --source . --remote origin --push \
    --description 'Psychology-informed persona construction and role simulation research framework'
fi
gh repo view "$repo_name" --json nameWithOwner,isPrivate,url
