import { Redirect, useLocalSearchParams } from "expo-router";

export default function ArtifactsScreen() {
  const params = useLocalSearchParams<{ sessionHandle?: string }>();
  return (
    <Redirect
      href={{
        pathname: "/(tabs)/assets",
        params: {
          sessionHandle: params.sessionHandle,
        },
      }}
    />
  );
}
