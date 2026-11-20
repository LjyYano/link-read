/** Share text and Markdown resolve to one video or podcast episode identity. */
export function normalizeVideoLink(source: string): string {
  let text = source.trim()
  if (/https?\\*:/i.test(text)) {
    text = text.replace(/\\+([:/?.=&_#%\-])/g, "$1").replace(/&amp;/g, "&")
    const matches = [...new Set((text.match(/https?:\/\/[^\s<>"\[\]【】「」]+/gi) ?? [])
      .map(value => value.replace(/[).,;!，。；！）']+$/, "")))]
    if (matches.length > 1) throw new Error("检测到多个链接，请一次提交一个视频或播客单集")
    if (matches.length) text = matches[0]
  }
  const authority = text.split("://")[1]?.split(/[/?#]/)[0] ?? ""
  if (!authority || authority.includes(":") || authority.includes("@")) throw new Error("来源链接不能包含端口或账号信息")
  const u = new URL(text)
  if (u.protocol !== "https:" || u.username || u.password || u.port) throw new Error("需要视频或播客单集的 HTTPS 链接")
  const path = u.pathname.split("/").filter(Boolean)
  if (["bilibili.com", "www.bilibili.com", "m.bilibili.com"].includes(u.hostname)) {
    const ids = u.searchParams.getAll("bvid")
    let id: string | undefined
    if (path.length === 2 && path[0] === "video") {
      id = path[1]
      if (ids.length && (ids.length !== 1 || ids[0] !== id)) throw new Error("视频编号冲突")
    } else if (["/list/watchlater", "/medialist/play/watchlater"].includes(u.pathname.replace(/\/$/, ""))) {
      id = ids.length === 1 ? ids[0] : undefined
    }
    const parts = u.searchParams.getAll("p")
    const p = parts[0] ?? "1"
    if (!id || !/^BV[A-Za-z0-9]{10}$/.test(id) || parts.length > 1 || !/^[0-9]{1,4}$/.test(p)
        || Number(p) < 1 || Number(p) > 1000) throw new Error("B 站视频编号或分 P 参数无效")
    return `https://www.bilibili.com/video/${id}?p=${Number(p)}`
  }
  if (["b23.tv", "www.b23.tv"].includes(u.hostname)) {
    if (!/^\/[A-Za-z0-9_-]+\/?$/.test(u.pathname)) throw new Error("B 站短链接无效")
    return `https://b23.tv/${path[0]}`
  }
  if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"].includes(u.hostname)) {
    const ids = u.searchParams.getAll("v")
    const id = ["youtu.be", "www.youtu.be"].includes(u.hostname) && path.length === 1 ? path[0]
      : path.length === 1 && path[0] === "watch" ? (ids.length === 1 ? ids[0] : undefined)
      : path.length === 2 && ["shorts", "embed", "live"].includes(path[0]) ? path[1] : undefined
    if (!id || !/^[A-Za-z0-9_-]{11}$/.test(id)) throw new Error("需要单个 YouTube 视频链接")
    return `https://www.youtube.com/watch?v=${id}`
  }
  if (u.hostname === "podcasts.apple.com") {
    // URL() removes dot segments; inspect the pasted path before that normalization.
    const rawPath = text.match(/^https:\/\/[^/?#]+([^?#]*)/i)?.[1] || ""
    if (rawPath.split("/").some(segment => /^(?:\.|%2e){1,2}$/i.test(segment))) {
      throw new Error("Apple 播客链接路径无效")
    }
    const match = u.pathname.match(/^\/(?:([A-Za-z]{2})\/)?podcast\/(?:[^/]+\/)?id([1-9][0-9]{0,19})\/?$/)
    const episodes = u.searchParams.getAll("i")
    if (!match || episodes.length !== 1 || !/^[1-9][0-9]{0,19}$/.test(episodes[0])) {
      throw new Error("请复制 Apple 播客的单集链接，需包含 i= 单集编号")
    }
    return `https://podcasts.apple.com/${(match[1] || "us").toLowerCase()}/podcast/id${match[2]}?i=${episodes[0]}`
  }
  throw new Error("支持 B 站、YouTube 视频及 Apple 播客单集链接")
}
