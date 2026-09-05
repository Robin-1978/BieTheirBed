import { Redirect, useLocalSearchParams } from "expo-router";

export default function NodeScreen() {
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  return (
    <Redirect
      href={{
        pathname: "/settings/node",
        params: {
          workspaceId: params.workspaceId,
          workspaceName: params.workspaceName,
          nodeId: params.nodeId,
        },
      }}
    />
  );
}
