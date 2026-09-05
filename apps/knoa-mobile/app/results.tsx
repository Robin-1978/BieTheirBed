import { Redirect, useLocalSearchParams } from "expo-router";

export default function ResultsScreen() {
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  return (
    <Redirect
      href={{
        pathname: "/(tabs)/assets",
        params: {
          workspaceId: params.workspaceId,
          workspaceName: params.workspaceName,
          nodeId: params.nodeId,
        },
      }}
    />
  );
}
