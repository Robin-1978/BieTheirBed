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
