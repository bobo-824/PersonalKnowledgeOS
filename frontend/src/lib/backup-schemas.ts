/**
 * 备份与导出数据的运行时契约（阶段 5，D-068/D-069）。
 * 字段和后端 schemas/backup_api.py 逐一对齐。
 */

import { z } from "zod"

export const backupSchema = z.object({
  filename: z.string(),
  size_bytes: z.number().int().nonnegative(),
  created_at: z.string(),
})

export const backupListSchema = z.object({
  backups: z.array(backupSchema),
  keep_count: z.number().int().positive(),
})

export type Backup = z.infer<typeof backupSchema>
export type BackupList = z.infer<typeof backupListSchema>
