import { useEffect, useState } from "react"
import { PlugZap, Save } from "lucide-react"

import { ApiErrorNotice, Notice } from "@/components/feedback/Notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { testAiConnection } from "@/lib/ai-api"
import { AI_ERROR_KIND_HINTS, type AiConnectionTest } from "@/lib/ai-schemas"
import { useAiConfig, useUpdateAiConfig } from "@/lib/ai-queries"

/**
 * AI 设置页。密钥**只能写进去、读不出来**（C6）：输入框留空 = 不修改，
 * 后端根本没有"返回完整密钥"的接口，所以也不存在"界面上泄露"这回事。
 *
 * 保存只提交**改过的字段**：后端按"字段出现与否"决定写不写 .env，
 * 把没改的字段也发上去等于无意义地重写用户的文件。
 */
export function SettingsPage() {
  const { data: config, isPending, error } = useAiConfig()
  const updateMutation = useUpdateAiConfig()

  const [baseUrl, setBaseUrl] = useState("")
  const [model, setModel] = useState("")
  const [timeoutSeconds, setTimeoutSeconds] = useState("60")
  const [apiKey, setApiKey] = useState("")
  const [jsonMode, setJsonMode] = useState(false)
  const [testResult, setTestResult] = useState<AiConnectionTest | null>(null)
  const [testing, setTesting] = useState(false)

  // config 从缓存到达时把表单灌一次。保存成功后 mutation 带回最新 config，
  // 这个 effect 再灌一次，输入框回到"与文件一致"的状态。
  useEffect(() => {
    if (!config) return
    setBaseUrl(config.base_url)
    setModel(config.model)
    setTimeoutSeconds(String(config.timeout_seconds))
    setJsonMode(config.json_mode)
    setApiKey("")
  }, [config])

  if (isPending) {
    return <p className="py-10 text-center text-sm text-muted-foreground">正在读取配置…</p>
  }
  if (error) {
    return <ApiErrorNotice error={error} />
  }
  if (!config) return null

  const changedFields: Record<string, unknown> = {}
  if (baseUrl !== config.base_url) changedFields.base_url = baseUrl
  if (model !== config.model) changedFields.model = model
  const timeoutNumber = Number(timeoutSeconds)
  if (Number.isFinite(timeoutNumber) && timeoutNumber !== config.timeout_seconds) {
    changedFields.timeout_seconds = timeoutNumber
  }
  if (jsonMode !== config.json_mode) changedFields.json_mode = jsonMode
  if (apiKey.trim().length > 0) changedFields.api_key = apiKey.trim()
  const hasChanges = Object.keys(changedFields).length > 0

  const handleSave = () => {
    updateMutation.mutate(changedFields, {
      onSuccess: () => setTestResult(null),
    })
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      // 测的是**已保存**的配置（后端按 .env 发起请求），不是输入框里的草稿
      setTestResult(await testAiConnection())
    } catch {
      setTestResult({
        ok: false,
        model: config.model || "(未填)",
        duration_ms: 0,
        error_kind: "unavailable",
        message: "请求没能发出去。请确认后端正在运行，然后重试。",
      })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-heading text-xl font-semibold">设置</h1>
        <p className="text-sm text-muted-foreground">
          AI 模型配置保存在项目根目录的 .env 文件里（已被 Git 忽略）。
          保存后立即生效，不需要重启服务。
        </p>
      </header>

      <ApiErrorNotice error={updateMutation.error} />

      {updateMutation.isSuccess && updateMutation.data && (
        <Notice tone="success">
          <p>{updateMutation.data.message}</p>
          {updateMutation.data.backup_file && (
            <p className="mt-1 text-xs text-muted-foreground">
              修改前的 .env 已备份到 {updateMutation.data.backup_file}
            </p>
          )}
        </Notice>
      )}

      {config.shadowed_keys.length > 0 && (
        <Notice tone="warning">
          <span>
            以下环境变量{" "}
            <code className="rounded bg-muted px-1">{config.shadowed_keys.join("、")}</code>{" "}
            正被操作系统的环境变量设置盖住：在这里修改它们不会生效，
            要改得去系统的环境变量设置里改。
          </span>
        </Notice>
      )}

      {config.hint && !config.configured && <Notice tone="info">{config.hint}</Notice>}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            AI 模型
            {config.configured ? (
              <Badge>已配置</Badge>
            ) : (
              <Badge variant="secondary">未配置完整</Badge>
            )}
          </CardTitle>
          <CardDescription>
            兼容 OpenAI 接口格式的服务商都可以用（DeepSeek、硅基流动、本地 Ollama 等）。
            密钥只保存在你电脑的 .env 文件里，不会上传，也不会显示在这里。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ai-base-url">API 地址</Label>
            <Input
              id="ai-base-url"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="例如 https://api.deepseek.com/v1（本地 Ollama：http://127.0.0.1:11434/v1）"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ai-model">模型名</Label>
            <Input
              id="ai-model"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="例如 deepseek-chat"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ai-api-key">API Key（密钥）</Label>
            <Input
              id="ai-api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={
                config.api_key_set && config.api_key_hint
                  ? `已设置（${config.api_key_hint}），留空表示不修改`
                  : "尚未设置，填入服务商给的密钥"
              }
              autoComplete="new-password"
              spellCheck={false}
            />
            <p className="text-xs text-muted-foreground">
              密钥写进去之后就只能替换、不能查看——连这个页面也只显示末 4 位。
              想删除密钥请直接编辑 .env 文件。
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ai-timeout">超时时间（秒，5 ~ 600）</Label>
            <Input
              id="ai-timeout"
              type="number"
              min={5}
              max={600}
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              className="max-w-40"
            />
          </div>

          <div className="flex items-center gap-2">
            <input
              id="ai-json-mode"
              type="checkbox"
              checked={jsonMode}
              onChange={(event) => setJsonMode(event.target.checked)}
              className="size-4 accent-primary"
            />
            <Label htmlFor="ai-json-mode" className="font-normal">
              让 AI 用严格的 JSON 格式回复（需要服务商支持，不确定就别开）
            </Label>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              className="min-h-11 px-4"
              disabled={!hasChanges || updateMutation.isPending}
              onClick={handleSave}
            >
              <Save aria-hidden="true" />
              {updateMutation.isPending ? "保存中…" : "保存"}
            </Button>
            <Button
              variant="outline"
              className="min-h-11 px-4"
              disabled={testing || !config.configured}
              onClick={handleTest}
            >
              <PlugZap aria-hidden="true" />
              {testing ? "测试中…" : "测试连接"}
            </Button>
          </div>
          {!hasChanges && (
            <p className="text-xs text-muted-foreground">
              修改任意一项后才能保存；「测试连接」用的是已保存的配置，改完记得先保存。
            </p>
          )}

          {testResult && (
            <Notice tone={testResult.ok ? "success" : "warning"}>
              <p>{testResult.message}</p>
              {testResult.ok ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  模型 {testResult.model} · 首字耗时 {testResult.duration_ms} 毫秒
                </p>
              ) : (
                testResult.error_kind &&
                AI_ERROR_KIND_HINTS[testResult.error_kind] && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {AI_ERROR_KIND_HINTS[testResult.error_kind]}
                  </p>
                )
              )}
            </Notice>
          )}
        </CardContent>
      </Card>

      <Notice tone="info">
        <p>向 AI 发送的数据范围（最小必要原则）：</p>
        <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">
          <li>对话时：只发当前对话里的消息，别的知识一概不发</li>
          <li>提取草稿 / 审查时：只发这一条知识的标题、领域和当前版本的理解</li>
          <li>密钥永不外发（它只在你电脑的 .env 里，用来向服务商证明身份）</li>
        </ul>
      </Notice>
    </div>
  )
}
