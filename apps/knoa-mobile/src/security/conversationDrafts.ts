import * as SecureStore from "expo-secure-store";

const PREFIX = "knoa.conversation-draft.v1.";
const PENDING = "_pending";

function key(sessionHandle: string): string {
  const stableHandle = sessionHandle || PENDING;
  return `${PREFIX}${stableHandle.replace(/[^A-Za-z0-9._-]/g, "_")}`;
}

export async function loadConversationDraft(sessionHandle: string): Promise<string> {
  return (await SecureStore.getItemAsync(key(sessionHandle))) ?? "";
}

export async function storeConversationDraft(sessionHandle: string, value: string): Promise<void> {
  if (!value) {
    await SecureStore.deleteItemAsync(key(sessionHandle));
    return;
  }
  await SecureStore.setItemAsync(key(sessionHandle), value);
}

export async function removeConversationDraft(sessionHandle: string): Promise<void> {
  await SecureStore.deleteItemAsync(key(sessionHandle));
}
