#!/usr/bin/env bash
# Modified for GridCraft-Next: independent packaging identity and notices.
# Repackage a GridCraft-Next Linux tarball as a single-file Flatpak bundle:
#
#   $DIST/gridcraft-next-<version>-linux-<arch>.flatpak
#
# Usage: packaging/linux/flatpak-bundle.sh [--no-test] [TARBALL]
#
# TARBALL defaults to $DIST/gridcraft-next-<version>-linux-<arch>.tar.gz from package.sh. The bundle
# is built for the host architecture (x86_64 or aarch64), which must match the tarball's.
# Needs flatpak, flatpak-builder and the SVG pixbuf loader (librsvg2-common) for the host's
# `appstreamcli compose`; the freedesktop runtime and SDK named in the manifest are
# installed per-user from Flathub. Unless --no-test, the bundle is then installed per-user and
# `gridcraft-next-cli --version` is run inside the sandbox as a smoke test.
# Manifest: packaging/linux/flatpak/io.github.xue_ding_e.gridcraft_next.bundle.yml.
set -euo pipefail
# shellcheck source=../env.sh
. "$(dirname "${BASH_SOURCE[0]}")/../env.sh"
HERE="$ROOT/packaging/linux"
APP_ID=io.github.xue_ding_e.gridcraft_next
FLATHUB=https://dl.flathub.org/repo/flathub.flatpakrepo

TEST=1
TARBALL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-test) TEST=0; shift ;;
    -h | --help) sed -n '2,14p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) TARBALL="$1"; shift ;;
  esac
done

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ;;
  aarch64 | arm64) ARCH=aarch64 ;;
  *) echo "unsupported architecture $ARCH" >&2; exit 2 ;;
esac
BASENAME="gridcraft-next-$VERSION-linux-$ARCH"
TARBALL="${TARBALL:-$DIST/$BASENAME.tar.gz}"
[ -f "$TARBALL" ] || { echo "error: $TARBALL not found (run packaging/linux/package.sh --formats tar first)" >&2; exit 1; }
for tool in flatpak flatpak-builder; do
  command -v "$tool" >/dev/null || { echo "error: $tool not found (apt install flatpak flatpak-builder librsvg2-common)" >&2; exit 1; }
done

echo "==> GridCraft-Next $VERSION Flatpak bundle for $ARCH from $(basename "$TARBALL")"

WORK="$CARGO_TARGET_DIR/flatpak-bundle"
rm -rf "$WORK"
mkdir -p "$WORK/stage"
tar -xzf "$TARBALL" -C "$WORK/stage" --strip-components=1
[ -x "$WORK/stage/bin/gridcraft-next" ] || { echo "error: $TARBALL has no bin/gridcraft-next" >&2; exit 1; }
cp "$HERE/flatpak/$APP_ID.bundle.yml" "$WORK/$APP_ID.yml"

flatpak remote-add --user --if-not-exists flathub "$FLATHUB"
# --disable-rofiles-fuse: no FUSE in containers and CI runners.
flatpak-builder --user --install-deps-from=flathub --disable-rofiles-fuse --force-clean \
  --default-branch=stable --repo="$WORK/repo" --state-dir="$WORK/state" "$WORK/build" "$WORK/$APP_ID.yml"

OUT="$DIST/$BASENAME.flatpak"
# --runtime-repo lets `flatpak install` fetch the freedesktop runtime from Flathub if missing.
flatpak build-bundle --runtime-repo="$FLATHUB" "$WORK/repo" "$OUT" "$APP_ID" stable
echo "wrote $OUT"

if [ "$TEST" = 1 ]; then
  flatpak install --user -y --noninteractive --reinstall "$OUT"
  flatpak info --user "$APP_ID"
  flatpak run --command=gridcraft-next-cli "$APP_ID" --version
  flatpak run --command=sh "$APP_ID" -c 'ls /app/share/applications /app/share/metainfo /app/share/mime/packages /app/share/icons/hicolor/scalable/apps'
fi
echo "==> done"
ls -lh "$OUT"
