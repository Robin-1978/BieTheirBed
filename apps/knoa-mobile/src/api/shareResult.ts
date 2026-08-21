import { EncodingType, documentDirectory, writeAsStringAsync } from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";

export async function shareResultText(name: string, text: string): Promise<void> {
  if (!documentDirectory) throw new Error("document_directory_unavailable");
  const safe = (name.trim() || "knoa-result").replace(/[^A-Za-z0-9._-]+/g, "_");
  const uri = `${documentDirectory}${safe}.md`;
  await writeAsStringAsync(uri, text, { encoding: EncodingType.UTF8 });
  await Sharing.shareAsync(uri, { mimeType: "text/markdown", dialogTitle: "分享 Knoa 结果" });
}

export async function shareResultJson(name: string, value: unknown): Promise<void> {
  if (!documentDirectory) throw new Error("document_directory_unavailable");
  const safe = (name.trim() || "knoa-result").replace(/[^A-Za-z0-9._-]+/g, "_");
  const uri = `${documentDirectory}${safe}.json`;
  await writeAsStringAsync(uri, JSON.stringify(value, null, 2), { encoding: EncodingType.UTF8 });
  await Sharing.shareAsync(uri, { mimeType: "application/json", dialogTitle: "分享 Knoa JSON" });
}

export async function shareResultPdf(name: string, text: string): Promise<void> {
  if (!documentDirectory) throw new Error("document_directory_unavailable");
  const safe = (name.trim() || "knoa-result").replace(/[^A-Za-z0-9._-]+/g, "_");
  const uri = `${documentDirectory}${safe}.pdf`;
  const lines = text.replace(/\r/g, "").split("\n").flatMap((line) => {
    const chunks: string[] = [];
    for (let index = 0; index < line.length; index += 90) chunks.push(line.slice(index, index + 90));
    return chunks.length ? chunks : [""];
  }).slice(0, 48);
  const stream = ["BT", "/F1 11 Tf", "50 780 Td", ...lines.flatMap((line, index) => [index ? "0 -15 Td" : "", `(${pdfEscape(line)}) Tj`]), "ET"].filter(Boolean).join("\n");
  const pdf = `%PDF-1.4\n1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>endobj\n4 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n5 0 obj<< /Length ${stream.length} >>stream\n${stream}\nendstream endobj\ntrailer<< /Root 1 0 R >>\n%%EOF`;
  await writeAsStringAsync(uri, pdf, { encoding: EncodingType.UTF8 });
  await Sharing.shareAsync(uri, { mimeType: "application/pdf", dialogTitle: "分享 Knoa PDF" });
}

function pdfEscape(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)").replace(/[^\x20-\x7E]/g, "?");
}
