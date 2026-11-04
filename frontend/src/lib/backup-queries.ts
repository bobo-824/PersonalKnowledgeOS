/**
 * 备份的 React Query 封装。新建备份成功后 invalidate 整个
 * `["backups"]` 前缀——列表立刻能看到新文件和被挤掉的旧文件。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { createBackup, listBackups } from "@/lib/backup-api"

export const backupKeys = {
  all: ["backups"] as const,
}

export function useBackups() {
  return useQuery({
    queryKey: backupKeys.all,
    queryFn: listBackups,
  })
}

export function useCreateBackup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createBackup,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backupKeys.all }),
  })
}
