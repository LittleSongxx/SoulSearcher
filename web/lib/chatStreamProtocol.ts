export interface LegacyChatStreamEvent {
  type: string
  data: any
}

export interface LegacyChatStreamState {
  buffer: string
}

export interface SseChatStreamState {
  buffer: string
}

export type ChatStreamProtocol = 'sse' | 'legacy'
export type ChatStreamState = LegacyChatStreamState | SseChatStreamState

interface ConsumeLegacyChatStreamOptions {
  flush?: boolean
}

type ConsumeChatStreamOptions = ConsumeLegacyChatStreamOptions

export function getChatStreamProtocol(): ChatStreamProtocol {
  const raw = (process.env.NEXT_PUBLIC_CHAT_STREAM_PROTOCOL || 'sse').trim().toLowerCase()
  return raw === 'legacy' ? 'legacy' : 'sse'
}

export function getChatStreamPath(protocol: ChatStreamProtocol): string {
  return protocol === 'legacy' ? '/api/chat' : '/api/chat/sse'
}

export function createLegacyChatStreamState(): LegacyChatStreamState {
  return { buffer: '' }
}

export function createSseChatStreamState(): SseChatStreamState {
  return { buffer: '' }
}

export function createChatStreamState(protocol: ChatStreamProtocol): ChatStreamState {
  return protocol === 'legacy' ? createLegacyChatStreamState() : createSseChatStreamState()
}

export function consumeLegacyChatStreamChunk(
  state: LegacyChatStreamState,
  chunk: string,
  options: ConsumeLegacyChatStreamOptions = {},
): LegacyChatStreamEvent[] {
  state.buffer += chunk

  const lines = state.buffer.split('\n')
  state.buffer = options.flush ? '' : lines.pop() ?? ''

  const events: LegacyChatStreamEvent[] = []

  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line || !line.startsWith('0:')) continue

    try {
      const event = JSON.parse(line.slice(2)) as LegacyChatStreamEvent
      if (event?.type) {
        events.push(event)
      }
    } catch (error) {
      if (!options.flush) {
        state.buffer = `${rawLine}\n${state.buffer}`
        break
      }

      console.error('Error parsing flushed stream line:', error)
    }
  }

  return events
}

export function consumeSseChatStreamChunk(
  state: SseChatStreamState,
  chunk: string,
  options: ConsumeChatStreamOptions = {},
): LegacyChatStreamEvent[] {
  state.buffer += chunk.replace(/\r\n/g, '\n')

  const parts = state.buffer.split('\n\n')
  const frames = options.flush ? parts : parts.slice(0, -1)
  state.buffer = options.flush ? '' : parts[parts.length - 1] ?? ''

  const events: LegacyChatStreamEvent[] = []

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
      events.push(payload as LegacyChatStreamEvent)
    } else if (eventType) {
      events.push({ type: eventType, data: payload })
    }
  }

  return events
}

export function consumeChatStreamChunk(
  protocol: ChatStreamProtocol,
  state: ChatStreamState,
  chunk: string,
  options: ConsumeChatStreamOptions = {},
): LegacyChatStreamEvent[] {
  if (protocol === 'legacy') {
    return consumeLegacyChatStreamChunk(state as LegacyChatStreamState, chunk, options)
  }
  return consumeSseChatStreamChunk(state as SseChatStreamState, chunk, options)
}
