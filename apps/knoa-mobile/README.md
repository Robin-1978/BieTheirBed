# Knoa Mobile

Expo/React Native personal Agent workbench for the versioned Knoa Secure
Gateway.

```bash
npm install
npm run contract
npm run typecheck
npm test
EXPO_NO_TELEMETRY=1 npx expo-doctor
npm start
```

Generate a short-lived QR payload on the service machine after configuring a
real `gateway_public_url`:

```bash
pca gateway pair --ttl 300
```

The mobile private key and short-lived session token are stored through the
platform secure store. The App never stores the Core local service token.

Expo Push registration becomes active in an EAS development/production build
with a real project ID. Notifications contain only a category plus opaque Task
and approval IDs, and open the corresponding `/tasks/<task-id>` route.

The task composer accepts documents, native voice recording and camera capture.
Voice bytes are uploaded as an Artifact and sent through the Gateway's standard
transcription endpoint; the App contains no speech-provider integration.

## Private Android updates

Knoa uses a private APK channel rather than an app store. Android still requires
every APK to be signed: use one owner-controlled signing key for the first
install and every later build. This is not store or platform signing, but the
key must not be rotated or Android will reject an in-place update.

Each native build must increase `expo.android.versionCode`. Publish the resulting
APK locally on the service machine:

```bash
scripts/build-mobile-apk.sh
KNOA_MOBILE_RELEASE_NOTES="新增私人自更新" scripts/publish-mobile-apk.sh
pca gateway release latest
```

The scripts load `/disk/dev/env.sh`, write build output to
`/disk/dev/knoa-mobile-out`, and use the private signing configuration in
`~/.pc-assistant/secrets/android`. The publishing command reads the version
directly from the APK manifest; no version arguments need to be repeated
manually.

The App checks the authenticated Gateway release manifest when the workbench
opens. Downloads use the native resumable downloader against the Gateway's HTTP
Range endpoint. Moving the App to the background saves the resume state. Before
opening Android's installer, the App verifies the complete file size and
SHA-256 digest.

Android always shows a system installation confirmation unless the device is
managed or rooted. The first installation must also allow this App to install
unknown-source packages. Expo Go cannot exercise this flow; use an installable
native APK signed by the same private key.

## Android Push configuration

Release builds require both files below in the private Android secrets
directory (by default `~/.pc-assistant/secrets/android`):

- `expo-project-id`: the Expo/EAS project UUID used to obtain an Expo Push token.
- `google-services.json`: the Firebase Android app configuration for
  `dev.knoa.mobile`.

The Expo project must also have the matching FCM v1 service-account credential
configured for Android Push delivery. The build script embeds the project ID,
copies `google-services.json` only into the temporary build mirror, and prints
prominent warnings when either configuration is missing. In that case the App
shows that server Push is not configured instead of silently claiming success.
