'use client'

import React, { useEffect, useRef, useState } from 'react'
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso'
import { ArrowDown, FileSearch, Loader2, PanelLeft, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MessageItem } from '@/components/chat/MessageItem'
import { ArtifactsPanel } from '@/components/chat/ArtifactsPanel'
import { Header } from '@/components/chat/Header'
import { ResearchInput } from './ResearchInput'
import { useChatHistory } from '@/hooks/useChatHistory'
import { useChatStream } from '@/hooks/useChatStream'
import { STORAGE_KEYS, DEFAULT_MODEL } from '@/lib/constants'
import { getApiBaseUrl } from '@/lib/api'
import { Message } from '@/types/chat'
import { cn } from '@/lib/utils'

interface ContinueResearchTarget {
  target_type: 'claim' | 'source' | 'gap' | 'section'
  target_index?: number
  target_text?: string
  instruction?: string
}

const starterTasks = [
  '调研 2025 年 Deep Research Agent 的主流架构、评测基准与安全风险，并输出带引用的系统报告。',
  '比较 OpenAI、Anthropic、Google、Perplexity 的 Deep Research 产品能力、引用质量与适用场景。',
  '分析一个开源项目的产品定位、架构、关键风险和 P0/P1/P2 改进路线图。',
  '围绕某个行业趋势收集一手来源，验证关键判断，并给出结论可信度。',
]

export function ResearchWorkspace() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [selectedModel, setSelectedModel] = useState(DEFAULT_MODEL)
  const [showScrollButton, setShowScrollButton] = useState(false)
  const [showMobileArtifacts, setShowMobileArtifacts] = useState(false)
  const [isArtifactsOpen, setIsArtifactsOpen] = useState(true)
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const virtuosoRef = useRef<VirtuosoHandle>(null)
  const lastAtBottom = useRef<boolean | null>(null)

  const {
    history,
    isHistoryLoading,
    saveToHistory,
    loadSession,
    deleteSession,
  } = useChatHistory()

  const {
    messages,
    setMessages,
    isLoading,
    currentStatus,
    setCurrentStatus,
    artifacts,
    setArtifacts,
    pendingInterrupt,
    setPendingInterrupt,
    threadId,
    setThreadId,
    processChat,
    handleStop,
    handleApproveInterrupt,
  } = useChatStream({ selectedModel })

  useEffect(() => {
    const savedModel = localStorage.getItem(STORAGE_KEYS.MODEL)
    if (savedModel) setSelectedModel(savedModel)
  }, [])

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.MODEL, selectedModel)
  }, [selectedModel])

  useEffect(() => {
    if (messages.length > 0 && currentSessionId && !isLoading) {
      saveToHistory(messages, currentSessionId)
    }
  }, [messages, currentSessionId, isLoading, saveToHistory])

  const resetWorkspace = () => {
    if (messages.length > 0) saveToHistory(messages, currentSessionId || undefined)
    setCurrentSessionId(null)
    setMessages([])
    setArtifacts([])
    setCurrentStatus('')
    setInput('')
    setThreadId(null)
    setPendingInterrupt(null)
    handleStop()
  }

  const handleResearchSelect = (id: string) => {
    if (messages.length > 0) saveToHistory(messages, currentSessionId || undefined)
    const loadedMessages = loadSession(id)
    if (loadedMessages) {
      setMessages(loadedMessages)
      setCurrentSessionId(id)
      setArtifacts([])
      setCurrentStatus('')
      setInput('')
      setThreadId(null)
      setPendingInterrupt(null)
      handleStop()
    }
  }

  const handleDeleteResearch = (id: string) => {
    deleteSession(id)
    if (currentSessionId === id) resetWorkspace()
  }

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!input.trim() || isLoading) return
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: input.trim(),
    }
    const nextMessages = [...messages, userMessage]
    setMessages(nextMessages)
    setInput('')
    if (!currentSessionId && nextMessages.length === 1) {
      const id = saveToHistory(nextMessages)
      if (id) setCurrentSessionId(id)
    } else if (currentSessionId) {
      saveToHistory(nextMessages, currentSessionId)
    }
    await processChat(nextMessages)
  }

  const handleEditMessage = async (id: string, newContent: string) => {
    const index = messages.findIndex(m => m.id === id)
    if (index === -1) return
    const updatedMessage = { ...messages[index], content: newContent }
    const nextMessages = [...messages.slice(0, index), updatedMessage]
    setMessages(nextMessages)
    if (updatedMessage.role === 'user') await processChat(nextMessages)
  }

  const handleContinueResearch = async (target: ContinueResearchTarget) => {
    if (!threadId) return
    setCurrentStatus('正在准备继续研究...')
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/sessions/${threadId}/continue-research`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(target),
      })
      if (!response.ok) throw new Error(`Continue research failed: ${response.status}`)
      const data = await response.json()
      setInput(String(data?.resume_input || target.target_text || '').trim())
      setCurrentStatus('继续研究提示已生成，发送即可继续')
      setTimeout(() => setCurrentStatus(''), 3000)
    } catch {
      setCurrentStatus('继续研究准备失败')
      setTimeout(() => setCurrentStatus(''), 3000)
    }
  }

  const handleAtBottomChange = (atBottom: boolean) => {
    if (lastAtBottom.current === atBottom) return
    lastAtBottom.current = atBottom
    setShowScrollButton(!atBottom)
  }

  const scrollToBottom = () => {
    const idx = messages.length - 1
    if (idx >= 0) {
      virtuosoRef.current?.scrollToIndex({ index: idx, align: 'end', behavior: 'smooth' })
    }
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground font-sans selection:bg-primary/20">
      <aside className={cn('hidden border-r bg-card/50 backdrop-blur-xl transition-all md:flex md:flex-col', sidebarOpen ? 'w-[280px]' : 'w-0 overflow-hidden border-r-0')}>
        <div className="flex h-full flex-col gap-3 p-3">
          <div className="flex items-center justify-between px-2 pt-1">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground">W</div>
              <div>
                <div className="text-sm font-bold tracking-tight">Weaver</div>
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Research Workspace</div>
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={() => setSidebarOpen(false)} className="h-7 w-7 text-muted-foreground">
              <PanelLeft className="h-4 w-4" />
            </Button>
          </div>

          <Button className="h-10 justify-start gap-2" variant="outline" onClick={resetWorkspace}>
            <Plus className="h-4 w-4" />
            New Research
          </Button>

          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/70">Research Sessions</div>
            {isHistoryLoading ? (
              <div className="space-y-2 px-1">
                {[1, 2, 3].map(i => <div key={i} className="h-8 rounded-md bg-muted/40 animate-pulse" />)}
              </div>
            ) : history.length === 0 ? (
              <div className="px-2 py-3 text-xs italic text-muted-foreground">No research sessions yet</div>
            ) : (
              <div className="space-y-1">
                {history.map(item => (
                  <div key={item.id} className="group relative">
                    <button onClick={() => handleResearchSelect(item.id)} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 pr-10 text-left text-sm text-muted-foreground transition hover:bg-muted/60 hover:text-foreground">
                      <FileSearch className="h-4 w-4 shrink-0" />
                      <span className="truncate">{item.title}</span>
                    </button>
                    <button onClick={() => handleDeleteResearch(item.id)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-100">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </aside>

      <main className="relative flex min-w-0 flex-1 flex-col">
        <Header
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
          selectedModel={selectedModel}
          onModelChange={setSelectedModel}
          onToggleArtifacts={() => setShowMobileArtifacts(!showMobileArtifacts)}
          hasArtifacts={artifacts.length > 0 || !!threadId}
        />

        <div className="min-h-0 flex-1">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center overflow-y-auto p-6">
              <div className="w-full max-w-4xl space-y-8 text-center">
                <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-primary/10 shadow-xl shadow-primary/10">
                  <FileSearch className="h-10 w-10 text-primary" />
                </div>
                <div className="space-y-3">
                  <h1 className="text-3xl font-bold tracking-tight md:text-4xl">Start a deep research workspace</h1>
                  <p className="mx-auto max-w-2xl text-muted-foreground md:text-lg">Weaver now focuses on scoped research sessions: planning, web evidence, source grounding, quality checks, and citation-rich reports.</p>
                </div>
                <div className="grid gap-3 text-left md:grid-cols-2">
                  {starterTasks.map(task => (
                    <button key={task} onClick={() => setInput(task)} className="rounded-2xl border bg-card/50 p-4 text-sm leading-relaxed transition hover:border-primary/30 hover:bg-card hover:text-primary">
                      {task}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <Virtuoso
              ref={virtuosoRef}
              data={messages}
              followOutput="auto"
              atBottomStateChange={handleAtBottomChange}
              className="scrollbar-thin scrollbar-thumb-muted/20"
              itemContent={(index, message) => (
                <div className="mx-auto max-w-5xl px-4 sm:px-0" data-index={index}>
                  <MessageItem key={message.id} message={message} onEdit={handleEditMessage} />
                </div>
              )}
              components={{
                Footer: () => (
                  <div className="mx-auto max-w-5xl px-4 pb-4 sm:px-0">
                    {currentStatus && (
                      <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground animate-in fade-in slide-in-from-bottom-2">
                        {isLoading && <Loader2 className="h-3 w-3 animate-spin text-primary" />}
                        <span className="font-medium animate-pulse">{currentStatus}</span>
                      </div>
                    )}
                    <div className="h-4" />
                  </div>
                ),
              }}
            />
          )}
        </div>

        <div className={cn('absolute bottom-24 right-6 z-30 transition-all duration-500', showScrollButton ? 'translate-y-0 opacity-100' : 'translate-y-10 opacity-0 pointer-events-none')}>
          <Button variant="outline" size="icon" className="rounded-full bg-background/80 shadow-lg backdrop-blur" onClick={scrollToBottom}>
            <ArrowDown className="h-4 w-4" />
          </Button>
        </div>

        {pendingInterrupt && (
          <div className="mx-4 mb-3 flex flex-col gap-2 rounded-xl border bg-amber-50 p-3 text-amber-900 shadow-sm">
            <div className="text-sm font-semibold">Research approval required</div>
            <div className="text-xs text-amber-800">{pendingInterrupt.message || pendingInterrupt?.prompts?.[0]?.message || 'Approve the requested action to continue.'}</div>
            <div className="flex gap-2">
              <Button size="sm" onClick={handleApproveInterrupt} disabled={isLoading}>Approve & Continue</Button>
              <Button size="sm" variant="ghost" onClick={() => setPendingInterrupt(null)} disabled={isLoading}>Dismiss</Button>
            </div>
          </div>
        )}

        <ResearchInput input={input} setInput={setInput} onSubmit={handleSubmit} isLoading={isLoading} onStop={handleStop} />
      </main>

      {(artifacts.length > 0 || threadId) && (
        <div className={cn('hidden flex-col border-l bg-card shadow-2xl transition-all xl:flex', isArtifactsOpen ? 'w-[420px]' : 'w-[50px]')}>
          <ArtifactsPanel artifacts={artifacts} threadId={threadId} isOpen={isArtifactsOpen} onToggle={() => setIsArtifactsOpen(!isArtifactsOpen)} onContinueResearch={handleContinueResearch} />
        </div>
      )}

      {showMobileArtifacts && (
        <div className="fixed inset-0 z-50 flex flex-col bg-background xl:hidden animate-in slide-in-from-right duration-300">
          <div className="flex items-center justify-between border-b p-4">
            <h2 className="font-semibold">Research Inspector</h2>
            <Button variant="ghost" size="icon" onClick={() => setShowMobileArtifacts(false)}>
              <X className="h-5 w-5" />
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            <ArtifactsPanel artifacts={artifacts} threadId={threadId} onContinueResearch={handleContinueResearch} />
          </div>
        </div>
      )}
    </div>
  )
}
