import { useCallback, useMemo, useState } from "react"
import {
  Network,
  Sparkles,
} from "lucide-react"
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react"

import "@xyflow/react/dist/style.css"

import { ApiErrorNotice, EmptyState, Notice } from "@/components/feedback/Notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Select } from "@/components/ui/select"
import {
  RELATION_TYPES,
  RELATION_TYPE_LABELS,
  relationTypeLabel,
  type DecideAction,
  type JudgeReport,
  type Relation,
} from "@/lib/relation-schemas"
import {
  useCandidates,
  useDecideRelation,
  useGraph,
  useGrowthStats,
  useJudgePairs,
  usePendingRelations,
  useUpdateRelationType,
} from "@/lib/relation-queries"

// ===== 成长统计卡 =====

function StatsCard() {
  const { data: stats, isPending, error } = useGrowthStats()
  if (isPending) return null
  if (error || !stats) return null

  const cells = [
    { label: "知识总数", value: stats.total_knowledge },
    { label: "近 7 天新增", value: stats.added_last_7_days },
    { label: "累计复习", value: stats.total_reviews },
    { label: "已确认连线", value: stats.total_relations },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>成长统计</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {cells.map((cell) => (
            <div key={cell.label} className="rounded-lg border border-border p-3">
              <p className="text-2xl font-semibold">{cell.value}</p>
              <p className="text-xs text-muted-foreground">{cell.label}</p>
            </div>
          ))}
        </div>
        {Object.keys(stats.domains).length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">领域分布：</span>
            {Object.entries(stats.domains).map(([domain, count]) => (
              <Badge key={domain} variant="outline">
                {domain} · {count}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ===== 图谱可视化（只画 accepted，工作流 E 硬要求）=====

function GraphView() {
  const { data: graph, isPending, error } = useGraph()

  const nodes: Node[] = useMemo(() => {
    if (!graph) return []
    return graph.nodes.map((n, index) => ({
      id: n.id,
      position: {
        // 简单网格布局：单人库的量级下够用，节点可以手动拖动调整
        x: (index % 4) * 230,
        y: Math.floor(index / 4) * 130,
      },
      data: { label: n.title },
    }))
  }, [graph])

  const edges: Edge[] = useMemo(() => {
    if (!graph) return []
    return graph.edges.map((e) => ({
      id: e.id,
      source: e.source_id,
      target: e.target_id,
      label: relationTypeLabel(e.relation_type),
    }))
  }, [graph])

  if (isPending) {
    return <p className="py-6 text-center text-sm text-muted-foreground">正在读取图谱…</p>
  }
  if (error) return <ApiErrorNotice error={error} />
  if (!graph || graph.nodes.length === 0) {
    return (
      <EmptyState
        icon={Network}
        title="还没有知识"
        description="先去知识库记下第一条知识，这里会画出它们之间的关系。"
      />
    )
  }
  if (graph.edges.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">
          已有 {graph.nodes.length} 条知识，还没有已确认的连线。在下方候选里让 AI 判断、
          然后确认，图谱就会长出连线。
        </p>
      </div>
    )
  }

  return (
    <div className="h-[420px] overflow-hidden rounded-lg border border-border">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: false }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

// ===== 待审核队列 =====

function TypeChanger({ relation }: { relation: Relation }) {
  const updateType = useUpdateRelationType()

  const handleChange = (value: string) => {
    if (value === "__custom__") {
      const custom = window.prompt("自定义关系类型（简短一点，例如「同源概念」）：")
      if (!custom || !custom.trim()) return
      updateType.mutate({ id: relation.id, relationType: custom.trim() })
      return
    }
    updateType.mutate({ id: relation.id, relationType: value })
  }

  return (
    <Select
      className="h-9 w-auto text-xs"
      value=""
      disabled={updateType.isPending}
      onChange={(event) => handleChange(event.target.value)}
      aria-label="修改关系类型"
    >
      <option value="" disabled>
        改类型…
      </option>
      {RELATION_TYPES.map((type) => (
        <option key={type} value={type}>
          {RELATION_TYPE_LABELS[type]}
        </option>
      ))}
      <option value="__custom__">自定义…</option>
    </Select>
  )
}

function PendingRow({ relation }: { relation: Relation }) {
  const decide = useDecideRelation()

  const handleDecide = (action: DecideAction) => {
    // 拒绝是终态且不可反悔：这对知识以后不会再被 AI 提出来（防骚扰的代价），
    // 所以多确认一次
    if (action === "rejected") {
      const ok = window.confirm(
        `拒绝「${relation.source_title} ↔ ${relation.target_title}」这条候选？\n\n` +
          "拒绝后这对知识不会再被 AI 提出来。数据保留但不可恢复审核状态。",
      )
      if (!ok) return
    }
    decide.mutate({ id: relation.id, action })
  }

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{relationTypeLabel(relation.relation_type)}</Badge>
        <span className="text-sm font-medium">
          {relation.source_title} → {relation.target_title}
        </span>
        <Badge variant="outline" className="ml-auto">
          把握 {Math.round(relation.confidence * 100)}%
        </Badge>
      </div>
      <p className="text-sm leading-relaxed break-words">{relation.explanation}</p>
      <p className="text-xs leading-relaxed break-words text-muted-foreground">
        依据：{relation.evidence}
      </p>
      <p className="text-xs text-muted-foreground">
        {relation.model ?? "未知模型"} 判断，等你确认。确认后才会出现在图谱里。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          className="min-h-9 px-3"
          disabled={decide.isPending}
          onClick={() => handleDecide("accepted")}
        >
          确认
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="min-h-9 px-3"
          disabled={decide.isPending}
          onClick={() => handleDecide("ignored")}
        >
          暂时忽略
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="min-h-9 px-3"
          disabled={decide.isPending}
          onClick={() => handleDecide("rejected")}
        >
          拒绝
        </Button>
        <TypeChanger relation={relation} />
      </div>
    </li>
  )
}

function PendingQueue() {
  const { data: pending, isPending, error } = usePendingRelations()

  if (isPending) {
    return <p className="text-sm text-muted-foreground">正在读取待审核队列…</p>
  }
  if (error) return <ApiErrorNotice error={error} />
  if (!pending || pending.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        队列是空的。在下方选中候选让 AI 判断，判断结果会到这里等你确认。
      </p>
    )
  }

  return (
    <ul className="flex flex-col gap-3">
      {pending.map((relation) => (
        <PendingRow key={relation.id} relation={relation} />
      ))}
    </ul>
  )
}

// ===== 候选（免费召回）=====

function CandidatesCard() {
  const { data: list, isPending, error } = useCandidates()
  const judge = useJudgePairs()
  const [report, setReport] = useState<JudgeReport | null>(null)

  const handleJudge = useCallback(() => {
    if (!list || list.candidates.length === 0) return
    const count = list.candidates.length
    // D-042：付费操作，点之前明示要发几次请求
    const ok = window.confirm(
      `让 AI 判断这 ${count} 对候选？\n\n将向 AI 发送 ${count} 次请求（每对一次）。` +
        "判断结果进入待审核队列，由你逐条确认。",
    )
    if (!ok) return
    judge.mutate(
      list.candidates.map((c) => ({ source_id: c.source_id, target_id: c.target_id })),
      { onSuccess: (result) => setReport(result) },
    )
  }, [list, judge])

  if (isPending) {
    return <p className="text-sm text-muted-foreground">正在召回候选…</p>
  }
  if (error) return <ApiErrorNotice error={error} />

  const candidates = list?.candidates ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          等待 AI 判断的候选
          {candidates.length > 0 && <Badge variant="outline">{candidates.length} 对</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-xs text-muted-foreground">
          召回是纯本地检索，不花 AI 请求。只有点「让 AI 判断」才发请求（每对一次），
          点之前会再次跟你确认。
        </p>

        {candidates.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            目前没有新候选。多记几条知识，或给已有的知识补理解，召回会自动把可能相关的排到这里。
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {candidates.map((pair) => (
              <li
                key={`${pair.source_id}-${pair.target_id}`}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2"
              >
                <span className="min-w-0 text-sm break-words">
                  {pair.source_title}
                  <span className="text-muted-foreground"> ↔ </span>
                  {pair.target_title}
                </span>
                {(pair.source_domain || pair.target_domain) && (
                  <span className="ml-auto text-xs text-muted-foreground">
                    {[pair.source_domain, pair.target_domain]
                      .filter((d, i, arr) => d && arr.indexOf(d) === i)
                      .join(" / ")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}

        <Button
          className="min-h-11 self-start px-4"
          disabled={candidates.length === 0 || judge.isPending}
          onClick={handleJudge}
        >
          <Sparkles aria-hidden="true" />
          {judge.isPending
            ? "AI 判断中…"
            : `让 AI 判断${candidates.length > 0 ? `（${candidates.length} 次请求）` : ""}`}
        </Button>

        {judge.error && <ApiErrorNotice error={judge.error} />}

        {report && (
          <Notice tone="info">
            判断完成：新建 {report.created} 条待审核，AI 认为不相关 {report.unrelated} 对
            {report.skipped > 0 && `，跳过重复 ${report.skipped} 对`}
            {report.failed.length > 0 && `，失败 ${report.failed.length} 对（失败不影响其余结果）`}。
          </Notice>
        )}
        {report && report.failed.length > 0 && (
          <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
            {report.failed.map((f) => (
              <li key={`${f.source_id}-${f.target_id}`}>
                {f.source_id} ↔ {f.target_id}：{f.message}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

// ===== 页面 =====

export function KnowledgeGraphPage() {
  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <header className="flex flex-col gap-3">
        <h1 className="page-title ">知识图谱</h1>
        <p className="text-sm text-muted-foreground">
          只有你亲手确认过的关系才会出现在图谱里（AI 判断的候选永远先等你审核）。
        </p>
      </header>

      <StatsCard />

      <section className="flex flex-col gap-2">
        <h2 className="font-heading text-sm font-medium">图谱</h2>
        <GraphView />
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-heading text-sm font-medium">待审核</h2>
          <p className="text-xs text-muted-foreground">
            拒绝过的候选不会再被提出来，忽略的暂时安静
          </p>
        </div>
        <PendingQueue />
      </section>

      <CandidatesCard />

      <Notice tone="info">
        关系类型图例：
        {RELATION_TYPES.map((type) => (
          <Badge key={type} variant="outline" className="ml-1">
            {RELATION_TYPE_LABELS[type]}
          </Badge>
        ))}
        。AI 也可能提出自定义类型，会显示原文。
      </Notice>
    </div>
  )
}
