/** Small excerpts only: never invent a cover topic or execute HTML from titles. */
export function coverText(title?: string): { label: string; tone: string; compact: string } {
  const clean = (title || "").normalize("NFC")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\.(srt|vtt|json)$/i, "").replace(/\s+/g, " ").trim()
  const tokens = clean.match(/[\p{L}\p{N}]+(?:[·・][\p{L}\p{N}]+)*/gu) || []
  const meaningful = tokens.join(" ") || "阅读笔记"
  // Prefer the short title prefix (often an author/topic); otherwise take a
  // legible excerpt. Array.from avoids slicing UTF-16 surrogate pairs.
  const prefix = clean.split(/[：:!?！？—|]/, 1)[0].replace(/^[《「【\s]+|[》」】\s]+$/g, "")
  const heading = prefix && Array.from(prefix).length <= 9 && /[\p{L}\p{N}]/u.test(prefix)
    ? prefix : meaningful
  const han = /\p{Script=Han}/u.test(heading)
  const label = han ? Array.from(heading).slice(0, 8).join("")
    : Array.from(heading.split(/\s+/).slice(0, 3).join(" ")).slice(0, 24).join("")
  let hash = 0
  for (const char of clean) hash = ((hash * 31) + char.codePointAt(0)!) >>> 0
  const tone = ["sage", "sand", "slate", "lilac"][hash % 4]
  return { label, tone, compact: Array.from(label).slice(0, han ? 4 : 12).join("") }
}
