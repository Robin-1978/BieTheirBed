import * as SecureStore from "expo-secure-store";

const PREFIX = "knoa.conversation-draft.v1.";

function key(sessionHandle: string): string {
  return `${PREFIX}${sessionHandle.replace(/[^A-Za-z0-9._-]/g, "_")}`;
}

export async function loadConversationDraft(sessionHandle: string): Promise<string> {
  if (!sessionHandle) return "";
  return (await SecureStore.getItemAsync(key(sessionHandle))) ?? "";
}

export async function storeConversationDraft(sessionHandle: string, value: string): Promise<void> {
  if (!sessionHandle) return;
  if (!value) {
    await SecureStore.deleteItemAsync(key(sessionHandle));
    return;
  }
  await SecureStore.setItemAsync(key(sessionHandle), value);
}

export async function removeConversationDraft(sessionHandle: string): Promise<void> {
  if (sessionHandle) await SecureStore.deleteItemAsync(key(sessionHandle));
}
