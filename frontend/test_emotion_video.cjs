const assert = require('node:assert/strict');
const { EmotionVideoController, CLIPS, decodeAgentEmotionEvent } = require('./emotion-video.js');

function classes() {
  const values = new Set();
  return { add: x => values.add(x), remove: x => values.delete(x),
    contains: x => values.has(x), toggle: (x, on) => on ? values.add(x) : values.delete(x) };
}
function video() {
  return { currentTime: 0, readyState: 2, paused: true, hidden: false, classList: classes(),
    play() { this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; }, load() {}, removeAttribute() {},
    addEventListener() {}, removeEventListener() {} };
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  const agent = { identity:'agent-1', sid:'A1', kind:'agent' };
  const room = { remoteParticipants:new Map([['agent-1',agent]]) };
  const packet = fields => new TextEncoder().encode(JSON.stringify({
    call_id:'call-1',sequence:1,state:'happy',issued_at:1000,ttl_ms:5000,...fields
  }));
  const decode = (payload, options={}) => decodeAgentEmotionEvent({
    payload,participant:agent,topic:'dunni.emotion.v1',room,callId:'call-1',
    isAgent:p=>p.kind==='agent',now:2000,...options
  });
  assert.equal(decode(packet({})).state,'happy');
  assert.equal(decode(packet({call_id:'other'})),null);
  assert.equal(decode(packet({state:'arbitrary'})),null);
  assert.equal(decode(packet({issued_at:-10000})),null);
  assert.equal(decode(packet({}),{topic:'other'}),null);
  assert.equal(decode(packet({}),{participant:{identity:'stranger',sid:'X'}}),null);
  assert.equal(decode(packet({}),{isAgent:()=>false}),null);

  const stage = { hidden: true, classList: classes() };
  const neutral = video();
  const clip = video();
  const states = [];
  const receipts = [];
  const controller = new EmotionVideoController({ stage, neutral, clip,
    blackout: { classList: classes() }, reducedMotion: { matches: false },
    onStateChange: state => states.push(state), onReceipt: receipt => receipts.push(receipt) });

  await controller.start();
  assert.equal(stage.hidden, false);
  assert.equal(neutral.paused, false);
  assert.equal(controller.acceptEvent({ sequence: 1, state: 'invented' }), false);
  neutral.currentTime = 15;
  assert.equal(controller.acceptEvent({ sequence: 1, state: 'happy' }), true);
  await wait(500);
  assert.equal(controller.phase, 'playing');
  assert.equal(clip.src, CLIPS.happy);
  assert.equal(neutral.paused, true);
  assert.equal(controller.neutralTime, 15);
  assert.equal(states.at(-1), 'happy');
  assert.deepEqual(receipts.slice(0,3).map(item=>item.type),
    ['requested','loaded','switch_completed']);
  clip.onended();
  await wait(500);
  assert.equal(controller.phase, 'neutral');
  assert.equal(neutral.currentTime, 15);
  assert.equal(neutral.paused, false);
  assert.equal(states.at(-1), 'neutral');
  assert.equal(controller.acceptEvent({ sequence: 1, state: 'sad' }), false);
  controller.setEnabled(false);
  assert.equal(controller.acceptEvent({ sequence: 2, state: 'sad' }), false);
  controller.setEnabled(true);
  assert.equal(controller.manualSelect('curious'), true);
  await wait(500);
  assert.equal(states.at(-1), 'curious');
  assert.equal(controller.acceptEvent({ sequence: 2, state: 'angry' }), false);
  assert.equal(controller.manualSelect('neutral'), true);
  await wait(500);
  assert.equal(states.at(-1), 'neutral');
  controller.stop();
  assert.equal(stage.hidden, true);
  assert.equal(neutral.paused, true);
  console.log('emotion video state transitions passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
