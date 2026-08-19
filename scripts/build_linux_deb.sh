#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 6 ]; then
    printf 'Usage: %s VERSION ARCH BUNDLE UPDATER TRUST_STORE OUTPUT_DIR\n' "$0" >&2
    exit 2
fi
VERSION="$1"
ARCH="$2"
BUNDLE="$3"
UPDATER="$4"
TRUST_STORE="$5"
OUTPUT_DIR="$6"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
STAGING="$(mktemp -d)"
trap 'rm -rf -- "$STAGING"' EXIT

case "$ARCH" in x86_64) DEB_ARCH=amd64 ;; aarch64) DEB_ARCH=arm64 ;; *) printf 'Unsupported arch: %s\n' "$ARCH" >&2; exit 2 ;; esac
mkdir -p "$STAGING/DEBIAN" "$STAGING/usr/lib/knoa/bootstrap" "$OUTPUT_DIR"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@ARCH@/$DEB_ARCH/g" "$ROOT/deploy/product/linux/deb-control" > "$STAGING/DEBIAN/control"
install -m 755 "$ROOT/deploy/product/linux/deb-postinst" "$STAGING/DEBIAN/postinst"
install -m 755 "$ROOT/deploy/product/linux/deb-prerm" "$STAGING/DEBIAN/prerm"
install -m 755 "$ROOT/deploy/product/linux/install-knoa-bundle.sh" "$STAGING/usr/lib/knoa/bootstrap/install-knoa-bundle.sh"
install -m 644 "$BUNDLE" "$STAGING/usr/lib/knoa/bootstrap/knoa-host.zip"
install -m 755 "$UPDATER" "$STAGING/usr/lib/knoa/bootstrap/knoa-update"
install -m 644 "$TRUST_STORE" "$STAGING/usr/lib/knoa/bootstrap/release-trust.json"
dpkg-deb --root-owner-group --build "$STAGING" "$OUTPUT_DIR/knoa_${VERSION}_${DEB_ARCH}.deb"
