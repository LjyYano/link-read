export type AsrOptions = {
  enabled: boolean
  reuseToken: boolean
  baseUrl: string
  model: string
  maxMinutes: number
  chunkMinutes: number
  ffmpegPath: string
}
export const defaultAsr: AsrOptions = {
  enabled: false, reuseToken: false,
  baseUrl: "https://dashscope.aliyuncs.com/api/v1", model: "paraformer-v2", maxMinutes: 120, chunkMinutes: 5, ffmpegPath: "",
}
export function loadAsrOptions(value: Partial<AsrOptions> | undefined): AsrOptions {
  return {
    enabled: value?.enabled === true,
    reuseToken: value?.reuseToken === true,
    baseUrl: typeof value?.baseUrl === "string" ? value.baseUrl : defaultAsr.baseUrl,
    model: "paraformer-v2",
    chunkMinutes: typeof value?.chunkMinutes === "number" && [0, 5, 10, 15].includes(value.chunkMinutes) ? value.chunkMinutes : 5,
    ffmpegPath: typeof value?.ffmpegPath === "string" && value.ffmpegPath.length <= 4096
      && !/[\u0000-\u001f\u007f]/.test(value.ffmpegPath) && (!value.ffmpegPath || value.ffmpegPath.startsWith("/"))
      ? value.ffmpegPath : "",
    maxMinutes: typeof value?.maxMinutes === "number" && Number.isInteger(value.maxMinutes)
      && value.maxMinutes >= 1 && value.maxMinutes <= 720 ? value.maxMinutes : 120,
  }
}
