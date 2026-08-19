#!/usr/bin/env bash
# Create a self-describing Android release bundle for one-click Windows Hub publication.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DISK_DEV="${DISK_DEV:-/disk/dev}"
SOURCE_APK="${1:-$DISK_DEV/knoa-mobile-out/app/outputs/apk/release/app-release.apk}"
OUTPUT_DIR="${2:-$DISK_DEV/knoa-mobile-out/release}"
APP_METADATA="${KNOA_MOBILE_APP_METADATA:-$REPO_ROOT/apps/knoa-mobile/app.json}"

if [[ ! -f "$SOURCE_APK" ]]; then
  echo "Signed APK not found: $SOURCE_APK" >&2
  exit 1
fi
if [[ ! -f "$APP_METADATA" ]]; then
  echo "Mobile app metadata not found: $APP_METADATA" >&2
  exit 1
fi

metadata="$(${NODE_BINARY:-node} -e '
const app = require(process.argv[1]).expo;
const values = [app.version, app.android.versionCode, app.android.package];
if (!values[0] || !Number.isInteger(values[1]) || !values[2]) process.exit(2);
process.stdout.write(values.join("\t"));
' "$APP_METADATA")"
IFS=$'\t' read -r version_name version_code package_id <<<"$metadata"

if [[ ! "$version_name" =~ ^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$ ]]; then
  echo "Invalid Android version name in $APP_METADATA" >&2
  exit 1
fi
if [[ ! "$version_code" =~ ^[1-9][0-9]*$ ]]; then
  echo "Invalid Android version code in $APP_METADATA" >&2
  exit 1
fi
if [[ "$package_id" != "dev.knoa.mobile" ]]; then
  echo "Unexpected Android package in $APP_METADATA: $package_id" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
apk_name="knoa-$version_name.apk"
apk_output="$OUTPUT_DIR/$apk_name"
manifest_output="$OUTPUT_DIR/knoa-$version_name.release.json"
publisher_name="Publish-Knoa-$version_name.cmd"
publisher_output="$OUTPUT_DIR/$publisher_name"
cp "$SOURCE_APK" "$apk_output"

size_bytes="$(wc -c < "$apk_output")"
sha256="$(sha256sum "$apk_output" | cut -d' ' -f1)"

${NODE_BINARY:-node} -e '
const fs = require("fs");
const [path, fileName, versionName, versionCode, packageId, sizeBytes, sha256] = process.argv.slice(1);
const release = {
  schema_version: 1,
  platform: "android",
  package_id: packageId,
  version_name: versionName,
  version_code: Number(versionCode),
  min_supported_version_code: 1,
  file_name: fileName,
  size_bytes: Number(sizeBytes),
  sha256,
};
fs.writeFileSync(path, JSON.stringify(release, null, 2) + "\n", "utf8");
' "$manifest_output" "$apk_name" "$version_name" "$version_code" "$package_id" "$size_bytes" "$sha256"

${NODE_BINARY:-node} -e '
const fs = require("fs");
const [path, apkName, versionName, versionCode] = process.argv.slice(1);
const lines = [
  "@echo off",
  "setlocal",
  "set \"PY=%ProgramData%\\Knoa\\Runtime\\venv\\Scripts\\python.exe\"",
  `set "APK=%~dp0${apkName}"`,
  "if not exist \"%PY%\" goto missing",
  "if not exist \"%APK%\" goto missing",
  `"%PY%" -m knoa_platform.hub.admin mobile-publish "%APK%" --root "%ProgramData%\\Knoa\\HostedHub" --min-version-code 1 --version-name "${versionName}" --version-code ${versionCode}`,
  "if errorlevel 1 goto failed",
  "\"%PY%\" -m knoa_platform.hub.admin mobile-latest --root \"%ProgramData%\\Knoa\\HostedHub\"",
  "if errorlevel 1 goto failed",
  "echo.",
  "echo Knoa Android App published successfully.",
  "echo https://knoa.tinydotdot.com/downloads/android/latest.apk",
  "pause",
  "exit /b 0",
  ":missing",
  "echo Knoa Hub runtime or APK is missing.",
  "pause",
  "exit /b 2",
  ":failed",
  "echo.",
  "echo Knoa Android App publication failed.",
  "pause",
  "exit /b 1",
];
fs.writeFileSync(path, lines.join("\r\n") + "\r\n", "ascii");
' "$publisher_output" "$apk_name" "$version_name" "$version_code"

printf 'release_apk=%s\n' "$apk_output"
printf 'release_metadata=%s\n' "$manifest_output"
printf 'windows_one_click=%s\n' "$publisher_output"
printf 'sha256=%s\n' "$sha256"
