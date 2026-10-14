// Run with Node: node frontend/test_transcript_retry.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const start = html.indexOf('  function saveFinalTranscript(');
const end = html.indexOf('  // 等待接听铃声', start);
assert(start >= 0 && end > start);
const source = html.slice(start, end);
const seen = [];
const outcomes = [503, 502, 200];
const savedTranscriptCalls = new Set();
const failedTranscriptCalls = new Set();
const storage = new Map();
const localStorage = {
  getItem: key => storage.get(key),
  setItem: (key, value) => storage.set(key, value),
  removeItem: key => storage.delete(key),
};
const execute = new Function('fetch', 'accountReady', 'CHAT_SESSION_KEY', 'localStorage',
  'apiUrl', 'savedTranscriptCalls', 'failedTranscriptCalls', 'room', 'callPhase', 'setStatus',
  `let transcriptSaveQueue=Promise.resolve(); ${source}
   return {saveFinalTranscript, drain:()=>transcriptSaveQueue};`);
const h = execute(async (_url, request) => {
  seen.push(JSON.parse(request.body));
  const status = outcomes.shift();
  return {ok:status===200, status, json:async()=>({session_id:'session-1'})};
}, Promise.resolve(), 'session-key', localStorage, value=>value,
savedTranscriptCalls, failedTranscriptCalls, null, 'idle', ()=>{});
h.saveFinalTranscript('call-1', 'speaker-1', 'segment-1', 'user', '你好');
h.drain().then(() => {
  assert.equal(seen.length, 3);
  assert.deepEqual(seen[0], seen[1]);
  assert.deepEqual(seen[1], seen[2]);
  assert(savedTranscriptCalls.has('call-1'));
  assert(!failedTranscriptCalls.has('call-1'));
  console.log('Bounded transcript retry preserves segment id: PASS');
}).catch(error => {console.error(error); process.exitCode = 1;});
