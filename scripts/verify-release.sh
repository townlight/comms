#!/usr/bin/env bash
set -euo pipefail

VERSION="0.2.0"

python_cmd() {
  local candidates=()
  if [[ -n "${TOWNLIGHT_COMMS_RELEASE_PYTHON:-}" ]]; then
    candidates+=("${TOWNLIGHT_COMMS_RELEASE_PYTHON}")
  fi
  candidates+=(python python3 "py -3")

  for candidate in "${candidates[@]}"; do
    if $candidate -c "import build, pytest, ruff" >/dev/null 2>&1
    then
      echo "$candidate"
      return
    fi
  done

  echo "ERROR: no Python candidate has build, pytest, and ruff installed" >&2
  exit 1
}

PYTHON="$(python_cmd)"

echo "==> Version surface check"
"$PYTHON" - <<'PY'
from pathlib import Path
import tomllib

root = Path.cwd()
pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
version = pyproject["project"]["version"]
init = (root / "townlight_comms" / "__init__.py").read_text(encoding="utf-8")
readme = (root / "README.md").read_text(encoding="utf-8")
changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
assert version == "0.2.0", version
assert '__version__ = "0.2.0"' in init
assert "Townlight Communications 0.2.0 development candidate" in readme
assert "## [Unreleased]" in changelog
assert "### 0.2.0 candidate" in changelog
assert "townlight-core 1.3.0.dev2" in readme
print("PASS: version surfaces synchronized")
PY

echo "==> Test suite"
"$PYTHON" -m pytest -q

echo "==> Documentation gate"
bash scripts/verify-docs.sh

echo "==> Placeholder import gate"
"$PYTHON" scripts/check-civiccore-placeholder-imports.py

echo "==> Ruff"
"$PYTHON" -m ruff check .

echo "==> Build artifacts"
mkdir -p "dist/${VERSION}"
"$PYTHON" -m build --outdir "dist/${VERSION}"
(
  cd "dist/${VERSION}"
  sha256sum "townlight_comms-${VERSION}-py3-none-any.whl" "townlight_comms-${VERSION}.tar.gz" > SHA256SUMS.txt
)
test -f "dist/${VERSION}/townlight_comms-${VERSION}-py3-none-any.whl"
test -f "dist/${VERSION}/townlight_comms-${VERSION}.tar.gz"
test -s "dist/${VERSION}/SHA256SUMS.txt"
echo "PASS: build artifacts and SHA256SUMS.txt created"

echo "VERIFY-RELEASE: PASSED"
