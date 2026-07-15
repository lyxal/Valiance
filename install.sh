#!/usr/bin/env bash
# Install script for Valiance.
# Usage: curl -fsSL https://github.com/lyxal/Valiance/releases/latest/download/install.sh | sh
set -euo pipefail

REPO="lyxal/Valiance"
INSTALL_DIR="${VALIANCE_INSTALL_DIR:-/usr/local/bin}"

os=$(uname -s)
case "$os" in
  Linux)  asset="valiance-linux" ;;
  Darwin) asset="valiance-macos" ;;
  *)
    echo "error: unsupported OS '$os'. On Windows, use the PowerShell installer instead:" >&2
    echo "  irm https://github.com/${REPO}/releases/latest/download/install.ps1 | iex" >&2
    exit 1
    ;;
esac

url="https://github.com/${REPO}/releases/latest/download/${asset}"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

echo "Downloading ${asset}..."
curl -fsSL "$url" -o "$tmp"
chmod +x "$tmp"

write() {
  local dest="$1"
  if [ -w "$INSTALL_DIR" ]; then
    cp "$tmp" "$dest"
  else
    echo "Requesting sudo to install into ${INSTALL_DIR}..."
    sudo cp "$tmp" "$dest"
  fi
}

mkdir -p "$INSTALL_DIR" 2>/dev/null || sudo mkdir -p "$INSTALL_DIR"
write "${INSTALL_DIR}/valiance"
write "${INSTALL_DIR}/vln"

echo "Installed 'valiance' and 'vln' to ${INSTALL_DIR}"

if ! command -v vln >/dev/null 2>&1; then
  echo ""
  echo "Note: ${INSTALL_DIR} is not on your PATH. Add this to your shell profile:"
  echo "  export PATH=\"${INSTALL_DIR}:\$PATH\""
fi