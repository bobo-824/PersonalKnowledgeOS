/**
 * 与后端 `/api/backups` 通信的出口。`request` 与 relation-api.ts 同一份写法。
 *
 * 下载导出文件不走 request（那是给 JSON 契约用的）：
 * 浏览器对带 Content-Disposition 的 GET 直接触发下载，用 <a href> 即可。
 */

import { z } from "zod"

import { httpErrorFrom } from "@/lib/api"
import { backupListSchema, backupSchema, type Backup, type BackupList } from "@/lib/backup-schemas"

const BACKUP_BASE = "/api/backups"

function networkError(): Error {
  return new Error(
    "连不上后端服务。请确认已经运行 start.ps1（或 start.sh），并且浏览器地址是 http://127.0.0.1:8000。",
  )
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: { method?: "GET" | "POST" } = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { method: options.method ?? "GET" })
  } catch {
    throw networkError()
  }
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // 响应体不是 JSON，交给 httpErrorFrom 的兜底文案
    }
    throw httpErrorFrom(response, body)
  }
  return schema.parse(await response.json())
}

export async function listBackups(): Promise<BackupList> {
  return request(BACKUP_BASE, backupListSchema)
}

export async function createBackup(): Promise<Backup> {
  return request(BACKUP_BASE, backupSchema, { method: "POST" })
}
