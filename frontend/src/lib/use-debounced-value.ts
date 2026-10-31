import { useEffect, useState } from "react"

/**
 * 延迟一个值的变化，用于搜索框。
 *
 * 不做防抖的话，用户每敲一个字就发一次请求：中文输入法在候选词阶段
 * 还会连续触发多次，白白打满后端，结果列表也会闪烁。
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])

  return debounced
}
