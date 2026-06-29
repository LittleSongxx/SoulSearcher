'use client'

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, ExternalLink, GitBranch, Layers, ListChecks, RefreshCw, Search, ShieldCheck, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { fetchResearchEvidence, fetchRunEvents } from '@/lib/researchApiClient'
import { EvidenceClaim, EvidenceResponse, EvidenceSource, PlanGraph, PlanTask, RunEventRecord } from '@/types/evidence'

interface ContinueTarget {
  target_type: 'claim' | 'source' | 'gap' | 'section'
  target_id?: string
  target_index?: number
  target_text?: string
  instruction?: string
}

interface EvidencePanelProps {
  threadId?: string | null
  onContinueResearch?: (target: ContinueTarget) => void
}

function shortUrl(url?: string) {
  if (!url) return ''
  try {
    const parsed = new URL(url)
    return `${parsed.hostname}${parsed.pathname === '/' ? '' : parsed.pathname}`
  } catch {
    return url
  }
}

function claimTone(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value.includes('support')) return 'text-emerald-600 bg-emerald-500/10'
  if (value.includes('contradict')) return 'text-red-600 bg-red-500/10'
  if (value.includes('unsupported')) return 'text-amber-600 bg-amber-500/10'
  return 'text-muted-foreground bg-muted'
}

function collectQualityGaps(data: EvidenceResponse | null): string[] {
  const seen = new Set<string>()
  const gaps: string[] = []
  for (const record of data?.quality_gates || []) {
    for (const gate of record.gates || []) {
      if (gate.status !== 'fail') continue
      const missing = gate.details?.missing_dimensions
      if (!Array.isArray(missing)) continue
      for (const item of missing) {
        const text = String(item || '').trim()
        if (!text || seen.has(text)) continue
        seen.add(text)
        gaps.push(text)
      }
    }
  }
  return gaps
}

function hasObject(value: unknown): value is Record<string, any> {
  return !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value as Record<string, any>).length > 0
}

function percent(value: unknown) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `${Math.round(num * 100)}%`
}

function todoTone(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'completed') return 'text-emerald-600 bg-emerald-500/10'
  if (value === 'running') return 'text-blue-600 bg-blue-500/10'
  if (value === 'blocked') return 'text-amber-600 bg-amber-500/10'
  if (value === 'cancelled') return 'text-muted-foreground bg-muted'
  return 'text-slate-600 bg-slate-500/10'
}

export function EvidencePanel({ threadId, onContinueResearch }: EvidencePanelProps) {
  const [data, setData] = useState<EvidenceResponse | null>(null)
  const [runEvents, setRunEvents] = useState<RunEventRecord[]>([])
  const [activeTab, setActiveTab] = useState<AuditTabKey>('sources')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  const loadEvidence = useCallback(async () => {
    if (!threadId) {
      setData(null)
      setError('')
      return
    }

    setIsLoading(true)
    setError('')
    try {
      const [evidence, events] = await Promise.all([
        fetchResearchEvidence(threadId),
        fetchRunEvents(threadId).catch(() => ({ events: [] })),
      ])
      setData(evidence)
      setRunEvents(events.events || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load evidence')
    } finally {
      setIsLoading(false)
    }
  }, [threadId])

  useEffect(() => {
    void loadEvidence()
  }, [loadEvidence])

  const sources = useMemo(() => data?.sources || data?.evidence_items || [], [data])
  const claims = useMemo(() => data?.claims || [], [data])
  const passages = useMemo(() => data?.passages || [], [data])
  const planGraph = data?.plan_graph
  const gaps = useMemo(() => collectQualityGaps(data), [data])
  const quality = data?.quality_summary || {}
  const pipeline = data?.research_pipeline || {}
  const sourceQuality = data?.source_quality || {}
  const readerPlan = data?.browser_reader_plan || {}
  const orchestration = data?.worker_orchestration || {}
  const branchDiagnostics = data?.branch_diagnostics || {}
  const briefReview = data?.brief_review || {}
  const fallback = data?.fallback || {}

  if (!threadId) {
    return (
      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        Evidence will appear after a research session starts.
      </div>
    )
  }

  if (isLoading && !data) {
    return (
      <div className="flex items-center gap-2 rounded-lg border p-4 text-sm text-muted-foreground">
        <RefreshCw className="h-4 w-4 animate-spin" />
        Loading evidence...
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Evidence Inspector</h3>
          <p className="text-xs text-muted-foreground">Sources, claims, passages, and quality gaps for this thread.</p>
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => void loadEvidence()} disabled={isLoading}>
          <RefreshCw className={isLoading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-destructive/10 p-3 text-xs text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {!data && !error && (
        <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          No evidence artifacts are available yet. Run Deep Research to populate this inspector.
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-3 gap-2 text-center text-xs">
            <Metric label="Sources" value={sources.length} />
            <Metric label="Claims" value={claims.length} />
            <Metric label="Plan" value={planGraph?.tasks?.length || 0} />
          </div>

          <AuditTabs
            activeTab={activeTab}
            onChange={setActiveTab}
            counts={{
              sources: sources.length,
              claims: claims.length,
              plan: planGraph?.tasks?.length || 0,
              events: runEvents.length,
              gaps: gaps.length,
            }}
          />

          <EvidenceAuditTab
            activeTab={activeTab}
            sources={sources as EvidenceSource[]}
            claims={claims}
            passages={passages}
            planGraph={planGraph}
            runEvents={runEvents}
            gaps={gaps}
            onContinueResearch={onContinueResearch}
          />

          {Object.keys(quality).length > 0 && (
            <Card className="border-none shadow-sm ring-1 ring-border/50">
              <CardHeader className="p-3">
                <CardTitle className="text-xs">Quality Summary</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 p-3 pt-0 text-xs text-muted-foreground">
                {Object.entries(quality).slice(0, 8).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-3">
                    <span className="truncate">{key}</span>
                    <span className="font-medium text-foreground">{String(value)}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {(hasObject(pipeline) || hasObject(sourceQuality) || hasObject(readerPlan) || hasObject(orchestration) || hasObject(branchDiagnostics) || hasObject(briefReview) || hasObject(fallback)) && (
            <Section title="Research Runtime">
              {hasObject(fallback) && (
                <RuntimeCard icon={<AlertCircle className="h-3.5 w-3.5" />} title="Strategy fallback" tone="warning">
                  <RuntimeRow label="from" value={fallback.source_strategy} />
                  <RuntimeRow label="to" value={fallback.fallback_strategy} />
                  <RuntimeRow label="reason" value={fallback.reason} />
                </RuntimeCard>
              )}
              {hasObject(briefReview) && (
                <RuntimeCard icon={<ShieldCheck className="h-3.5 w-3.5" />} title="ResearchBrief Review">
                  <RuntimeRow label="status" value={briefReview.status} />
                  <RuntimeRow label="approval" value={briefReview.approval_required ? 'required' : 'not required'} />
                  <RuntimeRow label="next" value={briefReview.next_action} />
                </RuntimeCard>
              )}
              {hasObject(pipeline) && (
                <RuntimeCard icon={<GitBranch className="h-3.5 w-3.5" />} title="Pipeline">
                  <RuntimeRow label="stages" value={pipeline.stage_count} />
                  <RuntimeRow label="research units" value={Array.isArray(pipeline.research_units) ? pipeline.research_units.length : 0} />
                  <RuntimeRow label="findings" value={Array.isArray(pipeline.sub_research_findings) ? pipeline.sub_research_findings.length : 0} />
                </RuntimeCard>
              )}
              {hasObject(orchestration) && (
                <RuntimeCard icon={<Users className="h-3.5 w-3.5" />} title="Worker Orchestration">
                  <RuntimeRow label="dispatch" value={orchestration.dispatch_model} />
                  <RuntimeRow label="workers" value={orchestration.worker_count} />
                  <RuntimeRow label="partial results" value={orchestration.partial_result_count} />
                </RuntimeCard>
              )}
              {hasObject(branchDiagnostics) && (
                <RuntimeCard icon={<GitBranch className="h-3.5 w-3.5" />} title="Branch Diagnostics">
                  <RuntimeRow label="branches" value={branchDiagnostics.branch_count} />
                  <RuntimeRow label="duplicate focus" value={branchDiagnostics.duplicate_focus_count} />
                  <RuntimeRow label="conflict hints" value={branchDiagnostics.conflict_hint_count} />
                </RuntimeCard>
              )}
              {hasObject(sourceQuality) && (
                <RuntimeCard icon={<ShieldCheck className="h-3.5 w-3.5" />} title="Source Quality">
                  <RuntimeRow label="diversity" value={percent(sourceQuality.source_diversity_score)} />
                  <RuntimeRow label="primary ratio" value={percent(sourceQuality.primary_source_ratio)} />
                  <RuntimeRow label="low-value ratio" value={percent(sourceQuality.low_value_source_ratio)} />
                </RuntimeCard>
              )}
              {hasObject(readerPlan) && (
                <RuntimeCard icon={<Layers className="h-3.5 w-3.5" />} title="Reader Plan">
                  <RuntimeRow label="mode" value={readerPlan.mode} />
                  <RuntimeRow label="actions" value={readerPlan.action_count} />
                  <RuntimeRow label="fetch failures" value={readerPlan.failure_count} />
                </RuntimeCard>
              )}
            </Section>
          )}

        </>
      )}
    </div>
  )
}

function Metric({ label, value }: { label: string, value: number }) {
  return (
    <div className="rounded-lg bg-muted/50 p-2">
      <div className="text-base font-semibold text-foreground">{value}</div>
      <div className="text-muted-foreground">{label}</div>
    </div>
  )
}

type AuditTabKey = 'sources' | 'claims' | 'plan' | 'events' | 'gaps'

function AuditTabs({ activeTab, onChange, counts }: { activeTab: AuditTabKey, onChange: (tab: AuditTabKey) => void, counts: Record<AuditTabKey, number> }) {
  const tabs: Array<{ key: AuditTabKey, label: string }> = [
    { key: 'sources', label: 'Sources' },
    { key: 'claims', label: 'Claims' },
    { key: 'plan', label: 'Plan' },
    { key: 'events', label: 'Events' },
    { key: 'gaps', label: 'Gaps' },
  ]
  return (
    <div className="grid grid-cols-5 gap-1 rounded-lg bg-muted/50 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          onClick={() => onChange(tab.key)}
          className={`rounded-md px-1.5 py-1.5 text-[11px] font-medium transition ${activeTab === tab.key ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
        >
          <span className="block truncate">{tab.label}</span>
          <span className="tabular-nums">{counts[tab.key] || 0}</span>
        </button>
      ))}
    </div>
  )
}

function EvidenceAuditTab({
  activeTab,
  sources,
  claims,
  passages,
  planGraph,
  runEvents,
  gaps,
  onContinueResearch,
}: {
  activeTab: AuditTabKey
  sources: EvidenceSource[]
  claims: EvidenceClaim[]
  passages: EvidenceResponse['passages']
  planGraph?: PlanGraph
  runEvents: RunEventRecord[]
  gaps: string[]
  onContinueResearch?: (target: ContinueTarget) => void
}) {
  if (activeTab === 'sources') {
    return (
      <Section title="Sources">
        {sources.length === 0 && <EmptyAuditState text="No sources registered yet." />}
        {sources.slice(0, 10).map((source, index) => (
          <SourceCard key={`${source.url || source.title}-${index}`} source={source} index={index} onContinueResearch={onContinueResearch} />
        ))}
        {(!sources.length && passages?.length) ? passages.slice(0, 6).map((passage, index) => (
          <div key={`${passage.snippet_hash || passage.url}-${index}`} className="rounded-lg bg-muted/40 p-3 text-xs">
            <div className="mb-1 flex items-center gap-1 text-muted-foreground">
              <CheckCircle2 className="h-3 w-3" /> {passage.heading || passage.page_title || shortUrl(passage.url)}
            </div>
            <p className="line-clamp-4 leading-relaxed">{passage.quote || passage.text}</p>
          </div>
        )) : null}
      </Section>
    )
  }

  if (activeTab === 'claims') {
    return (
      <Section title="Claims">
        {claims.length === 0 && <EmptyAuditState text="No claim records yet." />}
        {claims.slice(0, 8).map((claim, index) => (
          <ClaimCard key={`${claim.claim}-${index}`} claim={claim} index={index} onContinueResearch={onContinueResearch} />
        ))}
      </Section>
    )
  }

  if (activeTab === 'plan') {
    const tasks = planGraph?.tasks || []
    return (
      <Section title={`Plan Graph v${planGraph?.version || 1}`}>
        {tasks.length === 0 && <EmptyAuditState text="No plan graph tasks recorded yet." />}
        {hasObject(planGraph?.summary) && (
          <RuntimeCard icon={<GitBranch className="h-3.5 w-3.5" />} title="Plan Summary">
            <RuntimeRow label="status" value={planGraph?.status} />
            <RuntimeRow label="ready" value={planGraph?.summary?.ready} />
            <RuntimeRow label="completed" value={`${planGraph?.summary?.completed ?? 0}/${planGraph?.summary?.total ?? tasks.length}`} />
            <RuntimeRow label="progress" value={`${planGraph?.summary?.progress_percent ?? 0}%`} />
          </RuntimeCard>
        )}
        {tasks.slice(0, 16).map((task, index) => (
          <PlanTaskCard key={`${task.id}-${index}`} task={task} index={index} onContinueResearch={onContinueResearch} />
        ))}
      </Section>
    )
  }

  if (activeTab === 'events') {
    return (
      <Section title="Run Events">
        {runEvents.length === 0 && <EmptyAuditState text="No persisted run events yet." />}
        {runEvents.slice(-20).map((event) => (
          <RunEventCard key={`${event.run_id}-${event.seq}`} event={event} />
        ))}
      </Section>
    )
  }

  return (
    <Section title="Quality Gaps">
      {gaps.length === 0 && <EmptyAuditState text="No quality gaps recorded yet." />}
      {gaps.slice(0, 8).map((gap, index) => (
        <div key={`${gap}-${index}`} className="rounded-lg border p-3 text-xs">
          <div className="font-medium">{gap}</div>
          {onContinueResearch && (
            <Button className="mt-2 h-7 px-2 text-xs" variant="outline" onClick={() => onContinueResearch({ target_type: 'gap', target_index: index + 1, target_text: gap })}>
              <Search className="mr-1 h-3 w-3" /> Continue
            </Button>
          )}
        </div>
      ))}
    </Section>
  )
}

function EmptyAuditState({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
      {text}
    </div>
  )
}

function SourceCard({ source, index, onContinueResearch }: { source: EvidenceSource, index: number, onContinueResearch?: (target: ContinueTarget) => void }) {
  const title = source.title || shortUrl(source.url) || `Source ${index + 1}`
  return (
    <div className="rounded-lg border p-3 text-xs">
      <div className="font-medium leading-relaxed">{title}</div>
      {source.url && (
        <a className="mt-1 flex items-center gap-1 text-muted-foreground hover:text-primary" href={source.url} target="_blank" rel="noreferrer">
          <ExternalLink className="h-3 w-3" /> {shortUrl(source.url)}
        </a>
      )}
      {(source.snippet || source.summary) && <p className="mt-2 text-muted-foreground line-clamp-3">{source.snippet || source.summary}</p>}
      {onContinueResearch && (
        <Button className="mt-2 h-7 px-2 text-xs" variant="outline" onClick={() => onContinueResearch({ target_type: 'source', target_index: index + 1, target_text: title || source.url })}>
          <Search className="mr-1 h-3 w-3" /> Continue
        </Button>
      )}
    </div>
  )
}

function PlanTaskCard({ task, index, onContinueResearch }: { task: PlanTask, index: number, onContinueResearch?: (target: ContinueTarget) => void }) {
  const status = String(task.status || 'pending')
  const deps = task.deps || []
  const evidenceCount = task.evidence_ids?.length || 0
  return (
    <div className="rounded-lg border p-3 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium leading-relaxed">{task.title || `Plan task ${index + 1}`}</div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">{task.id}</div>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${todoTone(status)}`}>{status}</span>
      </div>
      {task.question && task.question !== task.title && <p className="mt-2 line-clamp-3 text-muted-foreground">{task.question}</p>}
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-muted-foreground">
        <RuntimeRow label="priority" value={task.priority} />
        <RuntimeRow label="attempts" value={task.attempts} />
        <RuntimeRow label="deps" value={deps.length ? deps.join(', ') : 'none'} />
        <RuntimeRow label="evidence" value={evidenceCount} />
      </div>
      {task.blocked_reason && <p className="mt-2 line-clamp-3 text-amber-600">{task.blocked_reason}</p>}
      {task.result_preview && <p className="mt-2 line-clamp-2 text-muted-foreground">{task.result_preview}</p>}
      {onContinueResearch && (
        <Button className="mt-2 h-7 px-2 text-xs" variant="outline" onClick={() => onContinueResearch({ target_type: 'gap', target_id: task.id, target_text: task.title })}>
          <Search className="mr-1 h-3 w-3" /> Continue
        </Button>
      )}
    </div>
  )
}

function RunEventCard({ event }: { event: RunEventRecord }) {
  const status = event.status || event.payload?.data?.status || ''
  return (
    <div className="rounded-lg border bg-muted/20 p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 font-medium">
          <ListChecks className="h-3.5 w-3.5" />
          <span className="truncate">{event.type}</span>
        </div>
        <div className="font-mono text-[10px] text-muted-foreground">#{event.seq}</div>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="truncate">{event.created_at || event.run_id}</span>
        {status && <span className="rounded bg-muted px-1.5 py-0.5">{status}</span>}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string, children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h4>
      <div className="space-y-2">{children}</div>
    </div>
  )
}

function RuntimeCard({ icon, title, tone, children }: { icon: React.ReactNode, title: string, tone?: 'warning', children: React.ReactNode }) {
  return (
    <div className={`rounded-lg border p-3 text-xs ${tone === 'warning' ? 'border-amber-500/30 bg-amber-500/10' : 'bg-muted/30'}`}>
      <div className="mb-2 flex items-center gap-2 font-medium">
        {icon}
        <span>{title}</span>
      </div>
      <div className="space-y-1 text-muted-foreground">{children}</div>
    </div>
  )
}

function RuntimeRow({ label, value }: { label: string, value: unknown }) {
  if (value === undefined || value === null || value === '') return null
  return (
    <div className="flex justify-between gap-3">
      <span className="shrink-0">{label}</span>
      <span className="truncate text-right font-medium text-foreground">{String(value)}</span>
    </div>
  )
}

function ClaimCard({ claim, index, onContinueResearch }: { claim: EvidenceClaim, index: number, onContinueResearch?: (target: ContinueTarget) => void }) {
  return (
    <div className="rounded-lg border p-3 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium leading-relaxed">{claim.claim || `Claim ${index + 1}`}</div>
        {claim.status && <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${claimTone(claim.status)}`}>{claim.status}</span>}
      </div>
      {claim.notes && <p className="mt-2 text-muted-foreground">{claim.notes}</p>}
      {claim.evidence_passages && claim.evidence_passages.length > 0 && (
        <div className="mt-2 text-muted-foreground">{claim.evidence_passages.length} passage evidence items</div>
      )}
      {onContinueResearch && (
        <Button className="mt-2 h-7 px-2 text-xs" variant="outline" onClick={() => onContinueResearch({ target_type: 'claim', target_index: index + 1, target_text: claim.claim })}>
          <Search className="mr-1 h-3 w-3" /> Continue
        </Button>
      )}
    </div>
  )
}
