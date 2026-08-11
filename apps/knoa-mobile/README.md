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

Task completion, failure and approval events are shown as in-App reminders while
the App is open. The standard SSE cursor replays events missed while the App was
closed; reminder state and unread status stay local to the device. Knoa does not
require Expo, Firebase or another remote Push provider.

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
