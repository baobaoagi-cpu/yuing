// Run with Node: node frontend/test_call_answer_timeout.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const begin = html.indexOf("  const ringback=new Audio('/yuling-media/ringback.wav')");
const end = html.indexOf('  async function startCall(){', begin);
assert(begin >= 0 && end > begin, 'call answer timeout source must exist');
const source = html.slice(begin, end);

function harness() {
  const lamps = [], phases = [], timers = new Map(), delays = [];
  let timerId = 0, pauseCount = 0;
  const callRoom = {};
  const Audio = class {
    pause() { pauseCount += 1; }
    set currentTime(value) { this._currentTime = value; }
  };
  const api = new Function('Audio', 'setTimeout', 'clearTimeout', 'setLamp',
    'setCallPhase', 'room', 'callAttemptId', 'callPhase', `${source}\n` +
    'return {armCallAnswerTimer, markCallAnswered, clearCallAnswerTimer};')(
    Audio,
    (callback, delay) => {
      const id = ++timerId;
      delays.push(delay);
      timers.set(id, () => { timers.delete(id); callback(); });
      return id;
    },
    id => timers.delete(id),
    (...args) => lamps.push(args),
    (...args) => phases.push(args),
    callRoom, 1, 'thinking',
  );
  return {api, callRoom, timers, delays, lamps, phases,
    get pauseCount() { return pauseCount; }};
}

{
  const h = harness();
  h.api.armCallAnswerTimer(h.callRoom, 1);
  assert.equal(h.timers.size, 1, 'silent remote track must keep the timer armed');
  assert.equal(h.delays[0], 20000, 'wait no more than 20 seconds for first response');
  [...h.timers.values()][0]();
  assert.deepEqual(h.phases.at(-1), ['error', '端倪语音响应超时，请挂断后重拨']);
  assert.equal(h.pauseCount, 1, 'timeout must stop ringback');
  assert.equal(h.lamps.at(-1)[0], 'tts');
  assert.equal(h.timers.size, 0, 'fired timeout must not remain pending');
  h.api.markCallAnswered();
  assert.deepEqual(h.phases.at(-1), ['speaking'], 'a late real response must recover the UI');
}

{
  const h = harness();
  h.api.armCallAnswerTimer(h.callRoom, 1);
  h.api.markCallAnswered();
  assert.equal(h.timers.size, 0, 'actual speaking must cancel timeout');
  assert.equal(h.pauseCount, 1, 'actual speaking must stop ringback');
  h.api.markCallAnswered();
  assert.equal(h.pauseCount, 1, 'subsequent audio frames must not repeat cleanup');
}

{
  const h = harness();
  h.api.armCallAnswerTimer(h.callRoom, 1);
  h.api.clearCallAnswerTimer();
  assert.equal(h.timers.size, 0, 'hangup must cancel pending timeout');
}

console.log('Call answer timeout and ringback lifecycle: PASS');
