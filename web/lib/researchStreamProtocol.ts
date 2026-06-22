export interface ResearchStreamEvent {
  type: string
  data: any
  research_event?: ResearchRunEvent
}

export interface ResearchRunEvent {
  type: string
  data: any
  source_event_type?: string
  sequence?: number
  event_id?: string
  schema_version?: number
  created_at?: string
}

export interface SseResearchStreamState {
  buffer: string
}

export type ResearchStreamProtocol = 'sse'
export type ResearchStreamState = SseResearchStreamState

interface ConsumeResearchStreamOptions {
  flush?: boolean
}

export function getResearchStreamProtocol(): ResearchStreamProtocol {
  return 'sse'
}

export function getResearchStreamPath(_protocol?: ResearchStreamProtocol): string {
  return '/api/research/sse'
}

export function createSseResearchStreamState(): SseResearchStreamState {
  return { buffer: '' }
}

export function createResearchStreamState(_protocol?: ResearchStreamProtocol): ResearchStreamState {
  return createSseResearchStreamState()
}

export function consumeSseResearchStreamChunk(
  state: SseResearchStreamState,
  chunk: string,
  options: ConsumeResearchStreamOptions = {},
): ResearchStreamEvent[] {
  state.buffer += chunk.replace(/\r\n/g, '\n')

  const parts = state.buffer.split('\n\n')
  const frames = options.flush ? parts : parts.slice(0, -1)
  state.buffer = options.flush ? '' : parts[parts.length - 1] ?? ''

  const events: ResearchStreamEvent[] = []

  for (const rawFrame of frames) {
    const lines = rawFrame.split('\n')
    let eventType = ''
    const dataLines: string[] = []

    for (const rawLine of lines) {
      const line = rawLine.trimEnd()
      if (!line || line.startsWith(':') || line.startsWith('id:') || line.startsWith('retry:')) continue
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }

    if (!eventType && dataLines.length === 0) continue

    let payload: any = dataLines.join('\n')
    if (typeof payload === 'string' && payload.trim()) {
      try {
        payload = JSON.parse(payload)
      } catch {
      }
    }

    if (payload?.type) {
      events.push(payload as ResearchStreamEvent)
    } else if (eventType) {
      events.push({ type: eventType, data: payload, research_event: payload?.research_event })
    }
  }

  return events
}

export function consumeResearchStreamChunk(
  _protocol: ResearchStreamProtocol,
  state: ResearchStreamState,
  chunk: string,
  options: ConsumeResearchStreamOptions = {},
): ResearchStreamEvent[] {
  return consumeSseResearchStreamChunk(state, chunk, options)
}
