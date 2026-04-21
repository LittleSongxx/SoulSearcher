'use client'

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Clock,
  Zap,
  AlertTriangle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Server,
  Brain,
  Search,
  Wrench,
  BarChart3,
  TrendingUp,
  Loader2,
  XCircle,
  CheckCircle2,
  FileText,
  ShieldCheck,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useI18n } from '@/lib/i18n/i18n-context'
import { getApiBaseUrl } from '@/lib/api'

// ─── Types ───────────────────────────────────────────────────────────────────

interface RunMetricsData {
  run_id: string
  model: string
  route: string
  started_at: string
  ended_at: string | null
  duration_ms: number
  event_count: number
  nodes_started: Record<string, number>
  nodes_completed: Record<string, number>
  errors: string[]
  cancelled: boolean
  evidence_summary?: {
    sources_count: number
    unsupported_claims_count: number
    freshness_ratio_30d: number | null
    citation_coverage: number | null
    query_coverage_score: number | null
    claim_verifier_total: number | null
    claim_verifier_verified: number | null
    claim_verifier_unsupported: number | null
    claim_verifier_contradicted: number | null
  }
}

interface PrometheusGauge {
  sse_connections: number
  ws_connections: number
  http_inprogress: number
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const mins = Math.floor(ms / 60_000)
  const secs = Math.round((ms % 60_000) / 1000)
  return `${mins}m ${secs}s`
}

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

function pct(val: number | null | undefined): string {
  if (val == null) return '—'
  return `${(val * 100).toFixed(0)}%`
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  subValue,
  color = 'text-primary',
}: {
  icon: React.ElementType
  label: string
  value: string | number
  subValue?: string
  color?: string
}) {
  return (
    <Card className="relative overflow-hidden">
      <CardContent className="p-4">
        <div className="flex items-center gap-3">
          <div className={cn('p-2 rounded-lg bg-muted/60', color)}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground truncate">{label}</p>
            <p className="text-xl font-bold tracking-tight">{value}</p>
            {subValue && <p className="text-[11px] text-muted-foreground mt-0.5">{subValue}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function NodeBar({ name, started, completed }: { name: string; started: number; completed: number }) {
  const ratio = started > 0 ? Math.min(1, completed / started) : 0
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-28 truncate font-mono text-muted-foreground" title={name}>{name}</span>
      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            ratio >= 1 ? 'bg-emerald-500' : ratio > 0 ? 'bg-amber-500' : 'bg-muted-foreground/30'
          )}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      <span className="w-12 text-right tabular-nums">{completed}/{started}</span>
    </div>
  )
}

function RunRow({ run, onSelect, isSelected }: { run: RunMetricsData; onSelect: (id: string) => void; isSelected: boolean }) {
  const isRunning = !run.ended_at
  const hasErrors = run.errors.length > 0

  return (
    <button
      onClick={() => onSelect(run.run_id)}
      className={cn(
        'w-full text-left px-3 py-2.5 rounded-lg border transition-all text-sm',
        'hover:bg-muted/50 hover:border-border',
        isSelected ? 'bg-muted/60 border-primary/30 shadow-sm' : 'border-transparent'
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {isRunning ? (
            <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin flex-shrink-0" />
          ) : run.cancelled ? (
            <XCircle className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
          ) : hasErrors ? (
            <AlertTriangle className="h-3.5 w-3.5 text-amber-500 flex-shrink-0" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 flex-shrink-0" />
          )}
          <span className="font-mono text-xs truncate">{run.run_id.slice(0, 20)}</span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {run.route && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {run.route}
            </Badge>
          )}
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {fmtDuration(run.duration_ms)}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3 mt-1 text-[11px] text-muted-foreground">
        <span>{run.model}</span>
        <span>·</span>
        <span>{fmtTime(run.started_at)}</span>
        <span>·</span>
        <span>{run.event_count} events</span>
      </div>
    </button>
  )
}

function EvidencePanel({ evidence }: { evidence: NonNullable<RunMetricsData['evidence_summary']> }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="space-y-1">
        <p className="text-[11px] text-muted-foreground flex items-center gap-1">
          <FileText className="h-3 w-3" /> Sources
        </p>
        <p className="text-lg font-bold">{evidence.sources_count}</p>
      </div>
      <div className="space-y-1">
        <p className="text-[11px] text-muted-foreground flex items-center gap-1">
          <ShieldCheck className="h-3 w-3" /> Citation Coverage
        </p>
        <p className="text-lg font-bold">{pct(evidence.citation_coverage)}</p>
      </div>
      <div className="space-y-1">
        <p className="text-[11px] text-muted-foreground flex items-center gap-1">
          <Search className="h-3 w-3" /> Query Coverage
        </p>
        <p className="text-lg font-bold">{pct(evidence.query_coverage_score)}</p>
      </div>
      <div className="space-y-1">
        <p className="text-[11px] text-muted-foreground flex items-center gap-1">
          <TrendingUp className="h-3 w-3" /> Freshness (30d)
        </p>
        <p className="text-lg font-bold">{pct(evidence.freshness_ratio_30d)}</p>
      </div>
      {evidence.claim_verifier_total != null && (
        <>
          <div className="col-span-2 pt-2 border-t">
            <p className="text-[11px] text-muted-foreground mb-2">Claim Verification</p>
            <div className="flex gap-2">
              <Badge variant="secondary" className="text-xs">
                Total: {evidence.claim_verifier_total}
              </Badge>
              <Badge className="text-xs bg-emerald-500/10 text-emerald-600 border-emerald-500/20">
                Verified: {evidence.claim_verifier_verified ?? 0}
              </Badge>
              {(evidence.claim_verifier_unsupported ?? 0) > 0 && (
                <Badge className="text-xs bg-amber-500/10 text-amber-600 border-amber-500/20">
                  Unsupported: {evidence.claim_verifier_unsupported}
                </Badge>
              )}
              {(evidence.claim_verifier_contradicted ?? 0) > 0 && (
                <Badge className="text-xs bg-red-500/10 text-red-600 border-red-500/20">
                  Contradicted: {evidence.claim_verifier_contradicted}
                </Badge>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ─── Main Component ──────────────────────────────────────────────────────────

export function MetricsDashboard() {
  const { t } = useI18n()
  const [runs, setRuns] = useState<RunMetricsData[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<RunMetricsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [gauges, setGauges] = useState<PrometheusGauge>({ sse_connections: 0, ws_connections: 0, http_inprogress: 0 })
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const base = getApiBaseUrl()

  const fetchRuns = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/runs`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setRuns(data.runs || [])
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [base])

  const fetchRunDetail = useCallback(async (runId: string) => {
    setDetailLoading(true)
    try {
      const res = await fetch(`${base}/api/runs/${runId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setSelectedRun(data)
    } catch {
      // detail not available — use list data
      const found = runs.find(r => r.run_id === runId)
      setSelectedRun(found || null)
    } finally {
      setDetailLoading(false)
    }
  }, [base, runs])

  const fetchGauges = useCallback(async () => {
    try {
      const res = await fetch(`${base}/metrics`)
      if (!res.ok) return
      const text = await res.text()
      const parseLine = (name: string) => {
        const match = text.match(new RegExp(`^${name}\\s+([\\d.]+)`, 'm'))
        return match ? parseFloat(match[1]) : 0
      }
      setGauges({
        sse_connections: parseLine('weaver_sse_active_connections'),
        ws_connections: parseLine('weaver_ws_active_connections'),
        http_inprogress: parseLine('weaver_http_inprogress'),
      })
    } catch {
      // Prometheus may be disabled
    }
  }, [base])

  // Initial + auto refresh
  useEffect(() => {
    fetchRuns()
    fetchGauges()
    if (!autoRefresh) return
    const timer = setInterval(() => {
      fetchRuns()
      fetchGauges()
    }, 5000)
    return () => clearInterval(timer)
  }, [fetchRuns, fetchGauges, autoRefresh])

  // Fetch detail when selection changes
  useEffect(() => {
    if (selectedRunId) fetchRunDetail(selectedRunId)
    else setSelectedRun(null)
  }, [selectedRunId, fetchRunDetail])

  // Summary stats
  const stats = useMemo(() => {
    const total = runs.length
    const running = runs.filter(r => !r.ended_at).length
    const errored = runs.filter(r => r.errors.length > 0).length
    const avgDuration = total > 0 ? runs.reduce((s, r) => s + r.duration_ms, 0) / total : 0
    const totalEvents = runs.reduce((s, r) => s + r.event_count, 0)
    return { total, running, errored, avgDuration, totalEvents }
  }, [runs])

  const sortedRuns = useMemo(() => {
    return [...runs].sort((a, b) => {
      // Running first, then by start time descending
      if (!a.ended_at && b.ended_at) return -1
      if (a.ended_at && !b.ended_at) return 1
      return new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
    })
  }, [runs])

  const detail = selectedRun
  const nodeEntries = detail
    ? Object.keys({ ...detail.nodes_started, ...detail.nodes_completed }).map(name => ({
        name,
        started: detail.nodes_started[name] || 0,
        completed: detail.nodes_completed[name] || 0,
      }))
    : []

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto p-6 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight flex items-center gap-2">
              <BarChart3 className="h-5 w-5 text-primary" />
              {t('metricsTitle')}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">{t('metricsDescription')}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={cn('gap-1.5 text-xs', autoRefresh && 'text-emerald-600')}
            >
              <Activity className={cn('h-3.5 w-3.5', autoRefresh && 'animate-pulse')} />
              {autoRefresh ? t('metricsAutoRefreshOn') : t('metricsAutoRefreshOff')}
            </Button>
            <Button variant="outline" size="sm" onClick={() => { fetchRuns(); fetchGauges() }} className="gap-1.5 text-xs">
              <RefreshCw className="h-3.5 w-3.5" />
              {t('metricsRefresh')}
            </Button>
          </div>
        </div>

        {/* Error Banner */}
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 flex-shrink-0" />
            {t('metricsConnectionError')}: {error}
          </div>
        )}

        {/* Summary Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard icon={Zap} label={t('metricsTotalRuns')} value={stats.total} color="text-blue-500" />
          <StatCard
            icon={Activity}
            label={t('metricsActiveNow')}
            value={stats.running}
            subValue={gauges.sse_connections > 0 ? `${gauges.sse_connections} SSE · ${gauges.ws_connections} WS` : undefined}
            color="text-emerald-500"
          />
          <StatCard icon={Clock} label={t('metricsAvgDuration')} value={fmtDuration(stats.avgDuration)} color="text-amber-500" />
          <StatCard
            icon={AlertTriangle}
            label={t('metricsErrors')}
            value={stats.errored}
            color={stats.errored > 0 ? 'text-red-500' : 'text-muted-foreground'}
          />
        </div>

        {/* Main Content: Run List + Detail */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          {/* Run List */}
          <Card className="lg:col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Server className="h-4 w-4 text-muted-foreground" />
                {t('metricsRunList')}
                <Badge variant="secondary" className="ml-auto text-[10px]">{runs.length}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-2 max-h-[520px] overflow-y-auto">
              {loading ? (
                <div className="flex items-center justify-center py-12 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  {t('metricsLoading')}
                </div>
              ) : sortedRuns.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground text-sm">
                  {t('metricsNoRuns')}
                </div>
              ) : (
                <div className="space-y-1">
                  {sortedRuns.map(run => (
                    <RunRow
                      key={run.run_id}
                      run={run}
                      onSelect={setSelectedRunId}
                      isSelected={selectedRunId === run.run_id}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Detail Panel */}
          <Card className="lg:col-span-3">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Brain className="h-4 w-4 text-muted-foreground" />
                {t('metricsRunDetail')}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 max-h-[520px] overflow-y-auto">
              {detailLoading ? (
                <div className="flex items-center justify-center py-16 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                </div>
              ) : !detail ? (
                <div className="text-center py-16 text-muted-foreground text-sm">
                  {t('metricsSelectRun')}
                </div>
              ) : (
                <div className="space-y-5">
                  {/* Run info */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                    <div>
                      <p className="text-[11px] text-muted-foreground">Model</p>
                      <p className="font-medium truncate">{detail.model}</p>
                    </div>
                    <div>
                      <p className="text-[11px] text-muted-foreground">Route</p>
                      <p className="font-medium">{detail.route || '—'}</p>
                    </div>
                    <div>
                      <p className="text-[11px] text-muted-foreground">{t('metricsDuration')}</p>
                      <p className="font-medium">{fmtDuration(detail.duration_ms)}</p>
                    </div>
                    <div>
                      <p className="text-[11px] text-muted-foreground">Events</p>
                      <p className="font-medium">{detail.event_count}</p>
                    </div>
                  </div>

                  {/* Status */}
                  <div className="flex items-center gap-2">
                    {!detail.ended_at ? (
                      <Badge className="bg-blue-500/10 text-blue-600 border-blue-500/20">Running</Badge>
                    ) : detail.cancelled ? (
                      <Badge variant="secondary">Cancelled</Badge>
                    ) : detail.errors.length > 0 ? (
                      <Badge className="bg-amber-500/10 text-amber-600 border-amber-500/20">Completed with errors</Badge>
                    ) : (
                      <Badge className="bg-emerald-500/10 text-emerald-600 border-emerald-500/20">Completed</Badge>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {fmtTime(detail.started_at)}
                      {detail.ended_at && ` → ${fmtTime(detail.ended_at)}`}
                    </span>
                  </div>

                  {/* Node execution */}
                  {nodeEntries.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                        <Wrench className="h-3 w-3" /> {t('metricsNodeExecution')}
                      </p>
                      <div className="space-y-1.5">
                        {nodeEntries.map(n => (
                          <NodeBar key={n.name} {...n} />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Evidence Summary */}
                  {detail.evidence_summary && detail.evidence_summary.sources_count > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                        <ShieldCheck className="h-3 w-3" /> {t('metricsEvidenceSummary')}
                      </p>
                      <EvidencePanel evidence={detail.evidence_summary} />
                    </div>
                  )}

                  {/* Errors */}
                  {detail.errors.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-red-500 uppercase tracking-wider flex items-center gap-1.5">
                        <AlertTriangle className="h-3 w-3" /> Errors ({detail.errors.length})
                      </p>
                      <div className="space-y-1">
                        {detail.errors.map((err, i) => (
                          <div key={i} className="text-xs font-mono bg-destructive/5 border border-destructive/20 rounded px-3 py-2 break-all">
                            {err}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
