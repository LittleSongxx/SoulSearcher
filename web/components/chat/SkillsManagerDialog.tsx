'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldAlert, Wand2 } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { getApiBaseUrl } from '@/lib/api'
import { cn } from '@/lib/utils'

interface SkillValidationIssue {
  severity: string
  message: string
  field?: string
  skill_id?: string
  source?: string
}

interface SkillContractField {
  name: string
  description: string
  type: string
  required: boolean
  examples?: string[]
}

interface SkillDependency {
  name: string
  type: string
  required: boolean
  description?: string
}

interface SkillPermissions {
  network: boolean
  filesystem: string
  shell: boolean
  browser: boolean
  sandbox: boolean
  external_apis: boolean
}

interface SkillValidationState {
  valid: boolean
  issues: SkillValidationIssue[]
}

interface SkillAdminSummary {
  id: string
  name: string
  name_en: string
  icon: string
  mode: string
  description: string
  description_en: string
  category: string
  tools: string[]
  version: string
  status: string
  tool_policy: string
  input_contract: SkillContractField[]
  output_contract: SkillContractField[]
  dependencies: SkillDependency[]
  permissions: SkillPermissions
  validation: SkillValidationState
  system_prompt?: string
}

interface SkillDetail extends SkillAdminSummary {
  system_prompt: string
}

interface SkillRegistrySnapshot {
  loaded_at: string
  total_files: number
  valid_count: number
  invalid_count: number
  disabled_count: number
  issues: SkillValidationIssue[]
  skills?: SkillAdminSummary[]
}

interface SkillsManagerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedSkillId: string | null
  onSelectableSkillsChange: (
    skills: Array<{
      id: string
      name: string
      name_en: string
      icon: string
      mode: string
      description: string
      description_en: string
    }>
  ) => void
  onInvalidateSelectedSkill: () => void
}

function buildSelectableSkills(skills: SkillAdminSummary[]) {
  return skills
    .filter((skill) => skill.status === 'enabled' && skill.validation?.valid)
    .map((skill) => ({
      id: skill.id,
      name: skill.name,
      name_en: skill.name_en,
      icon: skill.icon,
      mode: skill.mode,
      description: skill.description,
      description_en: skill.description_en,
    }))
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail =
      typeof data?.detail === 'string'
        ? data.detail
        : typeof data?.message === 'string'
          ? data.message
          : `Request failed (${response.status})`
    throw new Error(detail)
  }
  return data as T
}

function formatTimestamp(value?: string) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

export function SkillsManagerDialog({
  open,
  onOpenChange,
  selectedSkillId,
  onSelectableSkillsChange,
  onInvalidateSelectedSkill,
}: SkillsManagerDialogProps) {
  const [skills, setSkills] = useState<SkillAdminSummary[]>([])
  const [registry, setRegistry] = useState<SkillRegistrySnapshot | null>(null)
  const [activeSkillId, setActiveSkillId] = useState<string | null>(selectedSkillId)
  const [activeSkillDetail, setActiveSkillDetail] = useState<SkillDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [busyAction, setBusyAction] = useState<string | null>(null)

  const activeSkill = useMemo(
    () => skills.find((skill) => skill.id === activeSkillId) || null,
    [activeSkillId, skills]
  )

  const syncSelectableSkills = useCallback(
    (nextSkills: SkillAdminSummary[]) => {
      const selectableSkills = buildSelectableSkills(nextSkills)
      onSelectableSkillsChange(selectableSkills)
      if (
        selectedSkillId &&
        !selectableSkills.some((skill) => skill.id === selectedSkillId)
      ) {
        onInvalidateSelectedSkill()
      }
    },
    [onInvalidateSelectedSkill, onSelectableSkillsChange, selectedSkillId]
  )

  const loadAdminSkills = useCallback(async () => {
    setLoading(true)
    try {
      const data = await requestJson<{
        skills: SkillAdminSummary[]
        registry: SkillRegistrySnapshot
      }>('/api/skills?include_invalid=true&include_disabled=true')
      const nextSkills = Array.isArray(data.skills) ? data.skills : []
      setSkills(nextSkills)
      setRegistry(data.registry || null)
      syncSelectableSkills(nextSkills)
      setActiveSkillId((current) => {
        if (current && nextSkills.some((skill) => skill.id === current)) return current
        if (selectedSkillId && nextSkills.some((skill) => skill.id === selectedSkillId)) {
          return selectedSkillId
        }
        return nextSkills[0]?.id || null
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load skills')
    } finally {
      setLoading(false)
    }
  }, [selectedSkillId, syncSelectableSkills])

  const refreshFromSnapshot = useCallback(
    (snapshot: SkillRegistrySnapshot, successMessage: string) => {
      const nextSkills = Array.isArray(snapshot.skills) ? snapshot.skills : []
      setSkills(nextSkills)
      setRegistry(snapshot)
      syncSelectableSkills(nextSkills)
      setActiveSkillId((current) => {
        if (current && nextSkills.some((skill) => skill.id === current)) return current
        if (selectedSkillId && nextSkills.some((skill) => skill.id === selectedSkillId)) {
          return selectedSkillId
        }
        return nextSkills[0]?.id || null
      })
      toast.success(successMessage)
    },
    [selectedSkillId, syncSelectableSkills]
  )

  const handleRegistryAction = useCallback(
    async (path: string, successMessage: string, actionKey: string) => {
      setBusyAction(actionKey)
      try {
        const snapshot = await requestJson<SkillRegistrySnapshot>(path, {
          method: 'POST',
        })
        refreshFromSnapshot(snapshot, successMessage)
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Request failed')
      } finally {
        setBusyAction(null)
      }
    },
    [refreshFromSnapshot]
  )

  const handleToggleStatus = useCallback(
    async (skill: SkillAdminSummary) => {
      const nextAction = skill.status === 'enabled' ? 'disable' : 'enable'
      setBusyAction(`${nextAction}:${skill.id}`)
      try {
        const snapshot = await requestJson<SkillRegistrySnapshot>(
          `/api/skills/${skill.id}/${nextAction}`,
          { method: 'POST' }
        )
        refreshFromSnapshot(
          snapshot,
          nextAction === 'enable' ? `Enabled ${skill.name}` : `Disabled ${skill.name}`
        )
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Status update failed')
      } finally {
        setBusyAction(null)
      }
    },
    [refreshFromSnapshot]
  )

  useEffect(() => {
    if (!open) return
    void loadAdminSkills()
  }, [loadAdminSkills, open])

  useEffect(() => {
    if (!open || !activeSkillId) {
      setActiveSkillDetail(null)
      return
    }
    let cancelled = false
    const loadDetail = async () => {
      try {
        const detail = await requestJson<SkillDetail>(
          `/api/skills/${activeSkillId}?include_invalid=true`
        )
        if (!cancelled) {
          setActiveSkillDetail(detail)
        }
      } catch (error) {
        if (!cancelled) {
          setActiveSkillDetail(null)
          toast.error(error instanceof Error ? error.message : 'Failed to load skill detail')
        }
      }
    }
    void loadDetail()
    return () => {
      cancelled = true
    }
  }, [activeSkillId, open])

  const detail = activeSkillDetail || activeSkill

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl w-[95vw] p-0 overflow-hidden">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="flex items-center gap-2">
            <Wand2 className="h-5 w-5 text-amber-500" />
            Skill Management
          </DialogTitle>
          <DialogDescription>
            Reload, validate, inspect, and enable or disable skill manifests.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 px-6 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Total {registry?.total_files ?? skills.length}</Badge>
            <Badge variant="secondary">Valid {registry?.valid_count ?? 0}</Badge>
            <Badge variant={registry?.invalid_count ? 'destructive' : 'secondary'}>
              Invalid {registry?.invalid_count ?? 0}
            </Badge>
            <Badge variant="secondary">Disabled {registry?.disabled_count ?? 0}</Badge>
            <span className="text-xs text-muted-foreground ml-auto">
              Loaded {formatTimestamp(registry?.loaded_at)}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busyAction === 'reload'}
              onClick={() => void handleRegistryAction('/api/skills/reload', 'Skills reloaded', 'reload')}
            >
              <RefreshCw className={cn('mr-2 h-4 w-4', busyAction === 'reload' && 'animate-spin')} />
              Reload
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busyAction === 'validate'}
              onClick={() => void handleRegistryAction('/api/skills/validate', 'Validation finished', 'validate')}
            >
              <CheckCircle2 className="mr-2 h-4 w-4" />
              Validate
            </Button>
            <Button size="sm" variant="ghost" disabled={loading} onClick={() => void loadAdminSkills()}>
              Refresh List
            </Button>
          </div>

          <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)] min-h-[560px]">
            <div className="rounded-xl border overflow-hidden">
              <div className="border-b px-4 py-3 text-sm font-medium">Skill Catalog</div>
              <div className="max-h-[520px] overflow-y-auto p-2">
                {skills.length === 0 ? (
                  <div className="px-3 py-6 text-sm text-muted-foreground">
                    {loading ? 'Loading skills…' : 'No skills found.'}
                  </div>
                ) : (
                  skills.map((skill) => {
                    const invalidCount = skill.validation?.issues?.length || 0
                    const isActive = skill.id === activeSkillId
                    const toggleAction = skill.status === 'enabled' ? 'Disable' : 'Enable'
                    return (
                      <div
                        key={skill.id}
                        className={cn(
                          'mb-2 rounded-xl border p-3 transition-colors',
                          isActive ? 'border-amber-500/50 bg-amber-500/5' : 'hover:bg-muted/40'
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => setActiveSkillId(skill.id)}
                          className="flex w-full items-start gap-3 text-left"
                        >
                          <span className="text-lg leading-none">{skill.icon}</span>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="truncate font-medium">{skill.name}</span>
                              <Badge variant={skill.status === 'enabled' ? 'secondary' : 'outline'}>
                                {skill.status}
                              </Badge>
                            </div>
                            <div className="mt-1 truncate text-xs text-muted-foreground">
                              {skill.id} · {skill.mode} · v{skill.version}
                            </div>
                            <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                              {skill.description}
                            </div>
                          </div>
                        </button>
                        <div className="mt-3 flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            {skill.validation?.valid ? (
                              <span className="inline-flex items-center gap-1 text-emerald-600">
                                <CheckCircle2 className="h-3.5 w-3.5" /> Valid
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-destructive">
                                <AlertTriangle className="h-3.5 w-3.5" /> {invalidCount} issues
                              </span>
                            )}
                          </div>
                          <Button
                            size="sm"
                            variant={skill.status === 'enabled' ? 'outline' : 'default'}
                            disabled={busyAction === `enable:${skill.id}` || busyAction === `disable:${skill.id}`}
                            onClick={() => void handleToggleStatus(skill)}
                          >
                            {toggleAction}
                          </Button>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>
            </div>

            <div className="rounded-xl border overflow-hidden">
              <div className="border-b px-4 py-3 text-sm font-medium">Skill Detail</div>
              {detail ? (
                <div className="max-h-[520px] overflow-y-auto px-4 py-4 space-y-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-2xl leading-none">{detail.icon}</span>
                    <div>
                      <div className="text-lg font-semibold">{detail.name}</div>
                      <div className="text-sm text-muted-foreground">
                        {detail.id} · {detail.category} · {detail.mode} · v{detail.version}
                      </div>
                    </div>
                    <div className="ml-auto flex flex-wrap gap-2">
                      <Badge variant={detail.status === 'enabled' ? 'secondary' : 'outline'}>
                        {detail.status}
                      </Badge>
                      <Badge variant={detail.validation?.valid ? 'secondary' : 'destructive'}>
                        {detail.validation?.valid ? 'valid' : 'invalid'}
                      </Badge>
                      <Badge variant="outline">{detail.tool_policy}</Badge>
                    </div>
                  </div>

                  <div className="rounded-lg bg-muted/40 p-3 text-sm text-muted-foreground">
                    {detail.description}
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="rounded-lg border p-3">
                      <div className="mb-2 text-sm font-medium">Tools</div>
                      <div className="flex flex-wrap gap-2">
                        {detail.tools?.length ? (
                          detail.tools.map((tool) => (
                            <Badge key={tool} variant="outline">{tool}</Badge>
                          ))
                        ) : (
                          <span className="text-sm text-muted-foreground">No tools declared</span>
                        )}
                      </div>
                    </div>
                    <div className="rounded-lg border p-3">
                      <div className="mb-2 text-sm font-medium">Permissions</div>
                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <div>Network: {detail.permissions?.network ? 'yes' : 'no'}</div>
                        <div>Browser: {detail.permissions?.browser ? 'yes' : 'no'}</div>
                        <div>Shell: {detail.permissions?.shell ? 'yes' : 'no'}</div>
                        <div>Sandbox: {detail.permissions?.sandbox ? 'yes' : 'no'}</div>
                        <div>Filesystem: {detail.permissions?.filesystem || 'none'}</div>
                        <div>External APIs: {detail.permissions?.external_apis ? 'yes' : 'no'}</div>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="rounded-lg border p-3">
                      <div className="mb-2 text-sm font-medium">Input Contract</div>
                      <div className="space-y-2 text-sm">
                        {detail.input_contract?.length ? (
                          detail.input_contract.map((field) => (
                            <div key={`input-${field.name}`} className="rounded-md bg-muted/30 p-2">
                              <div className="font-medium">
                                {field.name}
                                <span className="ml-2 text-xs text-muted-foreground">
                                  {field.type}{field.required ? ' · required' : ''}
                                </span>
                              </div>
                              <div className="text-muted-foreground">{field.description}</div>
                            </div>
                          ))
                        ) : (
                          <div className="text-muted-foreground">No explicit input contract.</div>
                        )}
                      </div>
                    </div>
                    <div className="rounded-lg border p-3">
                      <div className="mb-2 text-sm font-medium">Output Contract</div>
                      <div className="space-y-2 text-sm">
                        {detail.output_contract?.length ? (
                          detail.output_contract.map((field) => (
                            <div key={`output-${field.name}`} className="rounded-md bg-muted/30 p-2">
                              <div className="font-medium">
                                {field.name}
                                <span className="ml-2 text-xs text-muted-foreground">
                                  {field.type}{field.required ? ' · required' : ''}
                                </span>
                              </div>
                              <div className="text-muted-foreground">{field.description}</div>
                            </div>
                          ))
                        ) : (
                          <div className="text-muted-foreground">No explicit output contract.</div>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-lg border p-3">
                    <div className="mb-2 text-sm font-medium">Dependencies</div>
                    <div className="space-y-2 text-sm">
                      {detail.dependencies?.length ? (
                        detail.dependencies.map((dependency) => (
                          <div key={dependency.name} className="flex items-start justify-between gap-3 rounded-md bg-muted/30 p-2">
                            <div>
                              <div className="font-medium">{dependency.name}</div>
                              <div className="text-muted-foreground">{dependency.description || dependency.type}</div>
                            </div>
                            <Badge variant={dependency.required ? 'secondary' : 'outline'}>
                              {dependency.required ? 'required' : 'optional'}
                            </Badge>
                          </div>
                        ))
                      ) : (
                        <div className="text-muted-foreground">No dependencies declared.</div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-lg border p-3">
                    <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                      {detail.validation?.valid ? (
                        <ShieldAlert className="h-4 w-4 text-emerald-600" />
                      ) : (
                        <AlertTriangle className="h-4 w-4 text-destructive" />
                      )}
                      Validation
                    </div>
                    <div className="space-y-2 text-sm">
                      {detail.validation?.issues?.length ? (
                        detail.validation.issues.map((issue, index) => (
                          <div key={`${issue.field || 'issue'}-${index}`} className="rounded-md border border-dashed p-2">
                            <div className="font-medium">
                              {issue.field || 'manifest'}
                              <span className="ml-2 text-xs uppercase text-muted-foreground">
                                {issue.severity}
                              </span>
                            </div>
                            <div className="text-muted-foreground">{issue.message}</div>
                          </div>
                        ))
                      ) : (
                        <div className="inline-flex items-center gap-2 text-emerald-600">
                          <CheckCircle2 className="h-4 w-4" /> No validation issues.
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-lg border p-3">
                    <div className="mb-2 text-sm font-medium">System Prompt</div>
                    <Textarea readOnly value={detail.system_prompt || ''} className="min-h-[220px] font-mono text-xs" />
                  </div>
                </div>
              ) : (
                <div className="flex h-full min-h-[520px] items-center justify-center text-sm text-muted-foreground">
                  Select a skill to inspect its manifest.
                </div>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
