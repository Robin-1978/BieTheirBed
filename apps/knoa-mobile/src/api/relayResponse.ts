const utf8Decoder = new TextDecoder("utf-8", { fatal: true });

export function relayResponseBody(
  bytes: Uint8Array,
  headers: Record<string, string>,
): string | ArrayBuffer {
  if (isUtf8TextContentType(headerValue(headers, "content-type"))) {
    return utf8Decoder.decode(bytes);
  }
  return exactArrayBuffer(bytes);
}

export function isUtf8TextContentType(value: string): boolean {
  const mediaType = value.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  return mediaType.startsWith("text/")
    || mediaType === "application/json"
    || mediaType.endsWith("+json")
    || mediaType === "application/x-ndjson"
    || mediaType === "application/ndjson"
    || mediaType === "application/json-seq";
}

function headerValue(headers: Record<string, string>, name: string): string {
  const target = name.toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === target) return value;
  }
  return "";
}

function exactArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}
