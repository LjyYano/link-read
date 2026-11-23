export const subtitleExtension = /\.(srt|vtt|json)$/i
export const maxSubtitleBytes = 16 * 1024 * 1024

export type ComposerPaste = { clipboard: DataTransfer | null; start: number; end: number }

export function pastedSubtitlePath(text: string): string | undefined {
  const path = text.trim()
  if (/[\r\n\0]/.test(path)) return undefined
  if (path.startsWith("/") && subtitleExtension.test(path)) return path
  if (!path.startsWith("file:")) return undefined
  try {
    const filename = decodeURIComponent(new URL(path).pathname)
    return subtitleExtension.test(filename) || /^\/\.file\/id=/.test(filename) ? path : undefined
  } catch { return undefined }
}

export function pastedSubtitleFormat(text: string): "srt" | "vtt" | "json" | undefined {
  const start = text.trimStart()
  if (/^WEBVTT(?:\s|$)/.test(start)) return "vtt"
  if (/^\{/.test(start) && /"(?:body|segments|schema_version)"\s*:/.test(start)) return "json"
  if (/(?:^|[\r\n])[\t ]*(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?[\t ]*-->/.test(start)) return "srt"
}
