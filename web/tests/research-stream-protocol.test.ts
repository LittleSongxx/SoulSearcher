import { test } from 'node:test'
import * as assert from 'node:assert/strict'

import {
  createResearchStreamState,
  createSseResearchStreamState,
  consumeResearchStreamChunk,
  consumeSseResearchStreamChunk,
  getResearchStreamPath,
  getResearchStreamProtocol,
} from '../lib/researchStreamProtocol'

test('parses standard SSE frames into research stream events', () => {
  const state = createSseResearchStreamState()
  const events = consumeSseResearchStreamChunk(
    state,
    'id: 1\nevent: text\ndata: {"type":"text","data":{"content":"hello"}}\n\n',
  )

  assert.deepEqual(events.map((event) => event.type), ['text'])
  assert.equal(events[0]?.data?.content, 'hello')
})

test('buffers partial SSE frames across chunks', () => {
  const state = createSseResearchStreamState()
  const first = consumeSseResearchStreamChunk(
    state,
    'event: status\ndata: {"type":"status","data":{"text":"work',
  )
  const second = consumeSseResearchStreamChunk(state, 'ing"}}\n\n')

  assert.deepEqual(first, [])
  assert.deepEqual(second.map((event) => event.type), ['status'])
  assert.equal(second[0]?.data?.text, 'working')
})

test('uses research SSE endpoint and protocol only', () => {
  assert.equal(getResearchStreamProtocol(), 'sse')
  assert.equal(getResearchStreamPath('sse'), '/api/research/sse')
})

test('generic stream consumer is SSE-only', () => {
  const protocol = getResearchStreamProtocol()
  const state = createResearchStreamState(protocol)
  const events = consumeResearchStreamChunk(
    protocol,
    state,
    'event: done\ndata: {"type":"done","data":{}}\n\n',
  )

  assert.deepEqual(events.map((event) => event.type), ['done'])
})

test('preserves canonical research event envelope', () => {
  const state = createSseResearchStreamState()
  const events = consumeSseResearchStreamChunk(
    state,
    'event: brief_created\ndata: {"type":"brief_created","data":{"thread_id":"t1"},"research_event":{"type":"brief.created","sequence":1}}\n\n',
  )

  assert.equal(events[0]?.type, 'brief_created')
  assert.equal(events[0]?.research_event?.type, 'brief.created')
  assert.equal(events[0]?.research_event?.sequence, 1)
})
