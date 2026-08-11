#!/usr/bin/env bash
# Build the owner-signed Knoa Android APK with caches and outputs on /disk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_MOBILE="$REPO/apps/knoa-mobile"
DISK_DEV="${DISK_DEV:-/disk/dev}"
ENV_SH="$DISK_DEV/env.sh"
ASSISTANT_HOME="${PC_ASSISTANT_HOME:-$HOME/.pc-assistant}"
SECRETS_DIR="${KNOA_MOBILE_SECRETS_DIR:-$ASSISTANT_HOME/secrets/android}"
KEY_PROPERTIES="$SECRETS_DIR/key.properties"
EXPO_PROJECT_ID_FILE="$SECRETS_DIR/expo-project-id"
GOOGLE_SERVICES_FILE="${KNOA_MOBILE_GOOGLE_SERVICES_JSON:-$SECRETS_DIR/google-services.json}"

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
if [[ -z "${KNOA_EXPO_PROJECT_ID:-}" && -f "$EXPO_PROJECT_ID_FILE" ]]; then
  KNOA_EXPO_PROJECT_ID="$(tr -d '[:space:]' < "$EXPO_PROJECT_ID_FILE")"
fi
if [[ -z "${KNOA_EXPO_PROJECT_ID:-}" ]]; then
  echo "WARNING: Push is not configured; set KNOA_EXPO_PROJECT_ID or create $EXPO_PROJECT_ID_FILE" >&2
else
  export KNOA_EXPO_PROJECT_ID
fi
if [[ ! -f "$GOOGLE_SERVICES_FILE" ]]; then
  echo "WARNING: Firebase Android Push configuration is missing: $GOOGLE_SERVICES_FILE" >&2
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
  --exclude='**/.gradle' \
  --exclude='**/.kotlin' \
  --exclude='**/.expo' \
  --exclude='**/.ruff_cache' \
  --exclude='/android/build' \
  --exclude='/android/app/build' \
  --exclude='**/android/build' \
  --exclude='**/.cxx' \
  --exclude='key.properties' \
  "$SOURCE_MOBILE/" "$MOBILE/"
rsync -a --delete --no-owner --no-group --no-perms \
  "$REPO/assets/branding/" "$KNOA_MOBILE_SOURCE_ROOT/assets/branding/"

cp "$KEY_PROPERTIES" "$ANDROID/key.properties"
if [[ -f "$GOOGLE_SERVICES_FILE" ]]; then
  cp "$GOOGLE_SERVICES_FILE" "$ANDROID/app/google-services.json"
fi

cleanup() {
  rm -f "$ANDROID/key.properties" "$ANDROID/app/google-services.json"
}
trap cleanup EXIT

echo "==> Knoa Android release build"
echo "    KNOA_MOBILE_SOURCE_ROOT=$KNOA_MOBILE_SOURCE_ROOT"
echo "    KNOA_MOBILE_BUILD_DIR=$KNOA_MOBILE_BUILD_DIR"
echo "    GRADLE_USER_HOME=$GRADLE_USER_HOME"
echo "    JAVA_HOME=$JAVA_HOME"

cd "$ANDROID"
bash ./gradlew \
  -PreactNativeArchitectures="${KNOA_MOBILE_ARCHITECTURES:-arm64-v8a}" \
  -Pandroid.enableMinifyInReleaseBuilds=true \
  -Pandroid.enableShrinkResourcesInReleaseBuilds=true \
  assembleRelease

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
