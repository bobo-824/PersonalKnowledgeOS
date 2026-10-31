/**
 * 时间与文本的显示格式化。
 *
 * 后端存的是 UTC，界面上必须换成用户本地时间——差 8 小时的话，
 * 用户会觉得"我刚记的东西怎么显示成昨天"。
 */

/** `2026年10月31日 19:08` */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

/** `刚刚` / `5 分钟前` / `3 天前`，超过 30 天退回绝对日期。 */
export function formatRelative(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso

  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 0) return formatDateTime(iso)
  if (seconds < 60) return "刚刚"
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)} 天前`
  return formatDateTime(iso)
}

/** 多行正文压成一行，用于列表摘要。 */
export function collapseWhitespace(text: string, maxLength = 80): string {
  const collapsed = text.replace(/\s+/g, " ").trim()
  if (collapsed.length <= maxLength) return collapsed
  return `${collapsed.slice(0, maxLength)}…`
}

/**
 * 是否包含至少一个字母、数字或汉字。
 *
 * 和后端 `KnowledgeCreate._require_real_understanding` 是同一条规则：
 * 「自己的理解」不能只填标点。前端先挡一道是为了即时反馈，
 * **后端仍然会再校验一次**——前端校验只是体验，不是安全边界。
 */
export function hasSubstantiveContent(text: string): boolean {
  // \p{L} 覆盖汉字与各类字母，\p{N} 覆盖数字；比 \w 靠谱，\w 不认汉字
  return /\p{L}|\p{N}/u.test(text)
}
