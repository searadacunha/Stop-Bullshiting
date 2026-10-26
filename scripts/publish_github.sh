#!/usr/bin/env bash
# Publish this source tree to a fresh public GitHub repo with GitHub CLI.
set -euo pipefail

OWNER="${GITHUB_OWNER:-searadacunha}"
REPO="Stop-Bullshiting"
TARGET="${OWNER}/${REPO}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo "Missing GitHub CLI. Install: brew install gh (macOS) or https://cli.github.com/" >&2
  exit 127
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI needs authentication: gh auth login" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "Missing git" >&2
  exit 127
fi
if [[ ! -d .git ]]; then
  git init -b main
fi
# Add only repository-authored paths, never arbitrary user secrets.
git add -- README.md LICENSE CHANGELOG.md CONTRIBUTING.md pyproject.toml .gitignore .github docs src tests scripts
if ! git diff --cached --quiet; then
  git commit -m "Initial release: Stop-Bullshiting"
fi

if gh repo view "$TARGET" >/dev/null 2>&1; then
  if git remote get-url origin >/dev/null 2>&1; then
    echo "Existing origin: $(git remote get-url origin)" >&2
    echo "For safety, this script will not overwrite an existing remote." >&2
    exit 2
  fi
  git remote add origin "https://github.com/${TARGET}.git"
  git push -u origin main
else
  gh repo create "$TARGET" --public --source . --remote origin --push \
    --description "Local-first context optimizer for coding agents"
fi
printf '\nPublished: https://github.com/%s\n' "$TARGET"
