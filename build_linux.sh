#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "[ERROR] Python not found in PATH." >&2
  exit 1
fi

echo "[INFO] Using Python command: ${PYTHON_CMD}"
echo "[INFO] Installing or upgrading PyInstaller..."
"${PYTHON_CMD}" -m pip install --upgrade pyinstaller

rm -rf build/pyinstaller-linux build/spec-linux dist/linux

echo "[INFO] Building Linux executable..."
"${PYTHON_CMD}" -m PyInstaller \
  --clean \
  --onefile \
  --name maven-push-tool \
  --distpath "dist/linux" \
  --workpath "build/pyinstaller-linux" \
  --specpath "build/spec-linux" \
  "push_maven_local.py"

chmod +x "dist/linux/maven-push-tool"
echo "[INFO] Build completed: dist/linux/maven-push-tool"
