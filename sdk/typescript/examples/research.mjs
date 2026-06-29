import { SoulSearcherClient } from '../dist/index.js'

const baseUrl = process.env.SOULSEARCHER_BASE_URL || 'http://127.0.0.1:8001'
const client = new SoulSearcherClient({ baseUrl })

const payload = {
  query: 'Give me a 3-bullet summary of SoulSearcher.',
}

for await (const ev of client.researchSse(payload)) {
  if (ev.type === 'text') process.stdout.write(String(ev.data?.content || ''))
  if (ev.type === 'done') break
}

process.stdout.write('\n')
console.log('thread_id:', client.lastThreadId)
