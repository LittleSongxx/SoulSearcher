import { test } from 'node:test'
import * as assert from 'node:assert/strict'

import {
  createLegacyChatStreamState,
  createSseChatStreamState,
  consumeLegacyChatStreamChunk,
  consumeSseChatStreamChunk,
  getChatStreamPath,
} from '../lib/chatStreamProtocol'

test('buffers partial legacy chat stream lines across chunks', () => {
  const state = createLegacyChatStreamState()

  const first = consumeLegacyChatStreamChunk(
    state,
    '0:{"type":"text","data":{"content":"Par',
  )
  const second = consumeLegacyChatStreamChunk(
    state,
    'is"}}\n0:{"type":"done","data":{}}\n',
  )

  assert.deepEqual(first, [])
  assert.deepEqual(second.map((event) => event.type), ['text', 'done'])
  assert.equal(second[0]?.data?.content, 'Paris')
})

test('ignores blank legacy chat stream lines', () => {
  const state = createLegacyChatStreamState()
  const events = consumeLegacyChatStreamChunk(
    state,
    '\n0:{"type":"status","data":{"text":"working"}}\n\n',
  )

  assert.deepEqual(events.map((event) => event.type), ['status'])
  assert.equal(events[0]?.data?.text, 'working')
})

test('parses standard SSE frames into chat stream events', () => {
  const state = createSseChatStreamState()
  const events = consumeSseChatStreamChunk(
    state,
    'id: 1\nevent: text\ndata: {"type":"text","data":{"content":"hello"}}\n\n',
  )

  assert.deepEqual(events.map((event) => event.type), ['text'])
  assert.equal(events[0]?.data?.content, 'hello')
})

test('buffers partial SSE frames across chunks', () => {
  const state = createSseChatStreamState()
  const first = consumeSseChatStreamChunk(
    state,
    'event: status\ndata: {"type":"status","data":{"text":"work',
  )
  const second = consumeSseChatStreamChunk(state, 'ing"}}\n\n')

  assert.deepEqual(first, [])
  assert.deepEqual(second.map((event) => event.type), ['status'])
  assert.equal(second[0]?.data?.text, 'working')
})

test('uses SSE chat endpoint by default and legacy endpoint for fallback', () => {
  assert.equal(getChatStreamPath('sse'), '/api/chat/sse')
  assert.equal(getChatStreamPath('legacy'), '/api/chat')
})
