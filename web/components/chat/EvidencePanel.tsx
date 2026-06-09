'use client'

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, ExternalLink, GitBranch, Layers, ListTodo, RefreshCw, Search, ShieldCheck, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { fetchResearchEvidence } from '@/lib/researchApiClient'
import { EvidenceClaim, EvidenceResponse, EvidenceSource } from '@/types/evidence'

interface ContinueTarget {
  target_type: 'claim' | 'source' | 'gap' | 'section'
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

function todoProgress(value: unknown) {
  const num = Number(value)
  if (!Number.isFinite(num)) return 0
  return Math.max(0, Math.min(100, Math.round(num)))
}

export function EvidencePanel({ threadId, onContinueResearch }: EvidencePanelProps) {
  const [data, setData] = useState<EvidenceResponse | null>(null)
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
      setData(await fetchResearchEvidence(threadId))
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
  const gaps = useMemo(() => collectQualityGaps(data), [data])
  const researchTodos = useMemo(() => data?.research_todos || [], [data])
  const quality = data?.quality_summary || {}
  const todoSummary = data?.todo_summary || {}
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
            <Metric label="Passages" value={passages.length} />
          </div>

          {researchTodos.length > 0 && (
            <Section title="Research Todos">
              <RuntimeCard icon={<ListTodo className="h-3.5 w-3.5" />} title="Progress">
                <RuntimeRow label="complete" value={`${todoSummary.completed ?? researchTodos.filter((todo) => todo.status === 'completed').length}/${todoSummary.total ?? researchTodos.length}`} />
                <RuntimeRow label="pending" value={todoSummary.pending} />
                <RuntimeRow label="running" value={todoSummary.running} />
                <RuntimeRow label="blocked" value={todoSummary.blocked} />
              </RuntimeCard>
              {researchTodos.slice(0, 8).map((todo, index) => {
                const status = String(todo.status || 'pending')
                const progress = todoProgress(todo.progress)
                return (
                  <div key={`${todo.id || todo.title}-${index}`} className="rounded-lg border p-3 text-xs">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="font-medium leading-relaxed">{todo.title || `Task ${index + 1}`}</div>
                        {todo.source && <div className="mt-1 text-muted-foreground">{todo.source}</div>}
                      </div>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${todoTone(status)}`}>{status}</span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-primary/70" style={{ width: `${progress}%` }} />
                    </div>
                    {todo.result_preview && <p className="mt-2 line-clamp-2 text-muted-foreground">{todo.result_preview}</p>}
                  </div>
                )
              })}
            </Section>
          )}

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

          {gaps.length > 0 && (
            <Section title="Quality Gaps">
              {gaps.slice(0, 5).map((gap, index) => (
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
          )}

          {claims.length > 0 && (
            <Section title="Claims">
              {claims.slice(0, 8).map((claim, index) => (
                <ClaimCard key={`${claim.claim}-${index}`} claim={claim} index={index} onContinueResearch={onContinueResearch} />
              ))}
            </Section>
          )}

          {sources.length > 0 && (
            <Section title="Sources">
              {(sources as EvidenceSource[]).slice(0, 10).map((source, index) => (
                <div key={`${source.url || source.title}-${index}`} className="rounded-lg border p-3 text-xs">
                  <div className="font-medium leading-relaxed">{source.title || shortUrl(source.url) || `Source ${index + 1}`}</div>
                  {source.url && (
                    <a className="mt-1 flex items-center gap-1 text-muted-foreground hover:text-primary" href={source.url} target="_blank" rel="noreferrer">
                      <ExternalLink className="h-3 w-3" /> {shortUrl(source.url)}
                    </a>
                  )}
                  {(source.snippet || source.summary) && <p className="mt-2 text-muted-foreground line-clamp-3">{source.snippet || source.summary}</p>}
                  {onContinueResearch && (
                    <Button className="mt-2 h-7 px-2 text-xs" variant="outline" onClick={() => onContinueResearch({ target_type: 'source', target_index: index + 1, target_text: source.title || source.url })}>
                      <Search className="mr-1 h-3 w-3" /> Continue
                    </Button>
                  )}
                </div>
              ))}
            </Section>
          )}

          {passages.length > 0 && (
            <Section title="Passages">
              {passages.slice(0, 6).map((passage, index) => (
                <div key={`${passage.snippet_hash || passage.url}-${index}`} className="rounded-lg bg-muted/40 p-3 text-xs">
                  <div className="mb-1 flex items-center gap-1 text-muted-foreground">
                    <CheckCircle2 className="h-3 w-3" /> {passage.heading || passage.page_title || shortUrl(passage.url)}
                  </div>
                  <p className="line-clamp-4 leading-relaxed">{passage.quote || passage.text}</p>
                </div>
              ))}
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
