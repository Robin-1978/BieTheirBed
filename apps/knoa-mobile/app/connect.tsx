import { Redirect } from "expo-router";

export default function LegacyConnectRedirect() {
  return <Redirect href="/account/login" />;
}
