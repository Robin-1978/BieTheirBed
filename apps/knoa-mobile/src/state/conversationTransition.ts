export function shouldResetConversation(
  previousSessionHandle: string,
  nextSessionHandle: string,
): boolean {
  return Boolean(
    previousSessionHandle
      && previousSessionHandle !== nextSessionHandle,
  );
}
