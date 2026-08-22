#!/usr/bin/env bash
# Build the owner-signed Knoa Android APK with caches and outputs on /disk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_MOBILE="$REPO/apps/knoa-mobile"
DISK_DEV="${DISK_DEV:-/disk/dev}"
ENV_SH="$DISK_DEV/env.sh"
ASSISTANT_HOME="${KNOA_HOME:-$HOME/.knoa}"
SECRETS_DIR="${KNOA_MOBILE_SECRETS_DIR:-$ASSISTANT_HOME/secrets/android}"
KEY_PROPERTIES="$SECRETS_DIR/key.properties"

"$SCRIPT_DIR/bump-version.sh" check >/dev/null

if [[ ! -f "$ENV_SH" ]]; then
  echo "Missing Android environment: $ENV_SH" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$ENV_SH"

: "${ANDROID_HOME:?}"
: "${GRADLE_USER_HOME:?}"
: "${JAVA_HOME:?}"

if [[ ! -f "$KEY_PROPERTIES" ]]; then
  echo "Missing release signing configuration: $KEY_PROPERTIES" >&2
  exit 1
fi

export KNOA_MOBILE_BUILD_DIR="${KNOA_MOBILE_BUILD_DIR:-$DISK_DEV/knoa-mobile-out}"
export KNOA_MOBILE_SOURCE_ROOT="${KNOA_MOBILE_SOURCE_ROOT:-$DISK_DEV/knoa-mobile-build-root}"
MOBILE="$KNOA_MOBILE_SOURCE_ROOT/apps/knoa-mobile"
ANDROID="$MOBILE/android"
export KNOA_MOBILE_ANDROID_ROOT="$ANDROID"
export NODE_ENV="${NODE_ENV:-production}"
mkdir -p "$KNOA_MOBILE_BUILD_DIR" "$MOBILE" "$KNOA_MOBILE_SOURCE_ROOT/assets" "$GRADLE_USER_HOME"

# CMake writes relative source dependencies into build.ninja. Redirecting only
# .cxx with symlinks breaks those relations after the link is resolved. Build a
# complete mirror on /disk instead. Receiver-only protect rules keep Gradle
# output produced inside dependencies (notably the React Native and Expo
# included builds) while normal source deletions still propagate through
# --delete. Plain excludes are not sufficient here: node_modules contains both
# shipped source build/ directories and Gradle-generated build/ directories.
# Never copy source-side Gradle/Kotlin histories, and do not synchronize Unix
# ownership or modes onto NTFS: both make unchanged inputs look modified.
rsync -a --delete --no-owner --no-group --no-perms \
  --filter='protect **/build/***' \
  --filter='protect **/.gradle/***' \
  --filter='protect **/.kotlin/***' \
  --filter='protect **/.cxx/***' \
  --filter='protect /node_modules/***' \
  --exclude='**/.gradle' \
  --exclude='**/.kotlin' \
  --exclude='**/.expo' \
  --exclude='node_modules' \
  --exclude='**/.ruff_cache' \
  --exclude='/android/build' \
  --exclude='/android/app/build' \
  --exclude='**/android/build' \
  --exclude='**/.cxx' \
  --exclude='key.properties' \
  "$SOURCE_MOBILE/" "$MOBILE/"
mkdir -p "$KNOA_MOBILE_SOURCE_ROOT/assets/branding"
rsync -a --delete --no-owner --no-group --no-perms \
  "$REPO/assets/branding/" "$KNOA_MOBILE_SOURCE_ROOT/assets/branding/"

cp "$KEY_PROPERTIES" "$ANDROID/key.properties"

# AGP 8.12 probes every cmake on PATH, including the broken Snap wrapper at
# /snap/bin/cmake on some build hosts. Give it an explicit SDK-style CMake
# installation rooted at the real system binary instead.
CMAKE_BIN="${KNOA_MOBILE_CMAKE_BIN:-/usr/bin/cmake}"
if [[ -x "$CMAKE_BIN" ]]; then
  CMAKE_ROOT="${KNOA_MOBILE_CMAKE_ROOT:-$KNOA_MOBILE_BUILD_DIR/cmake-system}"
  mkdir -p "$CMAKE_ROOT/bin"
  ln -sfn "$CMAKE_BIN" "$CMAKE_ROOT/bin/cmake"
  printf 'sdk.dir=%s\ncmake.dir=%s\n' "$ANDROID_HOME" "$CMAKE_ROOT" > "$ANDROID/local.properties"
fi

cleanup() {
  rm -f "$ANDROID/key.properties"
}
trap cleanup EXIT

echo "==> Knoa Android release build"
echo "    KNOA_MOBILE_SOURCE_ROOT=$KNOA_MOBILE_SOURCE_ROOT"
echo "    KNOA_MOBILE_BUILD_DIR=$KNOA_MOBILE_BUILD_DIR"
echo "    GRADLE_USER_HOME=$GRADLE_USER_HOME"
echo "    JAVA_HOME=$JAVA_HOME"

cd "$ANDROID"
GRADLE_RELEASE_ARGS=(
  -PreactNativeArchitectures="${KNOA_MOBILE_ARCHITECTURES:-arm64-v8a}"
  -Pandroid.enableMinifyInReleaseBuilds=true
  -Pandroid.enableShrinkResourcesInReleaseBuilds=true
)
if [[ "${KNOA_MOBILE_CLEAN_BUILD:-false}" == "true" ]]; then
  echo "    clean release: Gradle build cache disabled; all tasks rerun"
  GRADLE_RELEASE_ARGS+=(--no-build-cache --rerun-tasks)
fi
GRADLE_RELEASE_ARGS+=(assembleRelease)

GRADLE_BIN="${KNOA_MOBILE_GRADLE_BIN:-}"
if [[ -z "$GRADLE_BIN" ]]; then
  # Gradle 9.3.1 can hang during startup on the current build image. Prefer
  # an already-cached 9.1 binary when available; otherwise use the project's
  # wrapper as usual.
  GRADLE_BIN="$(find "$GRADLE_USER_HOME/wrapper/dists" -path '*/gradle-9.1.0/bin/gradle' -type f -print -quit 2>/dev/null || true)"
fi
if [[ -n "$GRADLE_BIN" && -x "$GRADLE_BIN" ]]; then
  "$GRADLE_BIN" "${GRADLE_RELEASE_ARGS[@]}"
else
  bash ./gradlew "${GRADLE_RELEASE_ARGS[@]}"
fi

BUILT_APK="$ANDROID/app/build/outputs/apk/release/app-release.apk"
APK="$KNOA_MOBILE_BUILD_DIR/app/outputs/apk/release/app-release.apk"
if [[ ! -f "$BUILT_APK" ]]; then
  echo "Release APK was not produced: $BUILT_APK" >&2
  exit 1
fi
mkdir -p "$(dirname "$APK")"
cp "$BUILT_APK" "$APK"

AAPT="$ANDROID_HOME/build-tools/$(ls "$ANDROID_HOME/build-tools" | sort -V | tail -1)/aapt"
APKSIGNER="$ANDROID_HOME/build-tools/$(ls "$ANDROID_HOME/build-tools" | sort -V | tail -1)/apksigner"

echo
"$AAPT" dump badging "$APK" | sed -n '1p'
"$APKSIGNER" verify --verbose --print-certs "$APK"
echo
echo "OK: $APK"
ls -lh "$APK"

echo
echo "==> Package self-describing release bundle"
"$SCRIPT_DIR/package-mobile-release.sh" "$APK" "$KNOA_MOBILE_BUILD_DIR/release"
