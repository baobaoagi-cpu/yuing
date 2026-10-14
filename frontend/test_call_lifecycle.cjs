// Run with Node: node frontend/test_call_lifecycle.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const begin = html.indexOf('  async function endCall(){');
const finish = html.indexOf('  function toggleMute(){', begin);
assert(begin >= 0 && finish > begin, 'call lifecycle source must exist');
const source = html.slice(begin, finish);

function makeEndCall(room, cleanup, timeout) {
  return new Function('room', 'cleanup', 'setTimeout', `${source}\nreturn endCall;`)(
    room, cleanup, timeout,
  );
}

(async () => {
  let settled = false;
  const hangingRoom = { disconnect: () => new Promise(() => {}) };
  const endCall = makeEndCall(hangingRoom, () => { settled = true; }, callback => callback());
  const pending = endCall();
  assert.equal(settled, true, 'hangup must update local call state immediately');
  await pending;

  let cancelledDial = false;
  await makeEndCall(null, () => { cancelledDial = true; }, callback => callback())();
  assert.equal(cancelledDial, true, 'hangup must work before a room is created');
  console.log('Call hangup with stalled disconnect and pre-room dial: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
