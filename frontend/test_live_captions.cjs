// Run with Node: node frontend/test_live_captions.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const begin = html.indexOf("callRoom.registerTextStreamHandler('lk.transcription'");
const end = html.indexOf('room.on(LK.RoomEvent.TrackSubscribed', begin);
assert(begin >= 0 && end > begin, 'LiveKit transcription handler must exist');
const source = html.slice(begin, end);

const shown = [], saved = [];
let handler;
const callRoom = {
  localParticipant: {identity:'self'},
  registerTextStreamHandler: (_topic, callback) => {handler = callback;},
};
new Function('room','callRoom','attempt','callAttemptId','callId',
  'showLiveTranscript','saveFinalTranscript','setLamp',source)(
    callRoom,callRoom,1,1,'test-call',
    (...args)=>shown.push(args),(...args)=>saved.push(args),()=>{});

function reader(chunks, final) {
  return {
    info:{attributes:{'lk.transcription_final':String(final),'lk.segment_id':'segment-a'}},
    async *[Symbol.asyncIterator]() {for(const chunk of chunks) yield chunk;},
  };
}

(async()=>{
  await handler(reader(['你','好'],false),{identity:'agent'});
  assert.deepEqual(shown.map(x=>x[2]),['你','你好']);
  assert.equal(saved.length,0,'interim words must never be persisted');
  await handler(reader(['你','好','。'],true),{identity:'agent'});
  assert.equal(shown.at(-1)[2],'你好。');
  assert.equal(shown.at(-1)[3],true);
  assert.equal(saved.length,1);
  assert.equal(saved[0][4],'你好。','the final receipt must contain the whole sentence');
  console.log('LiveKit chunk accumulation and final-only save: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
