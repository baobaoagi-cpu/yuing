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
    requestVideoFrameCallback(callback) { setTimeout(callback, 0); },
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
  const acks = [];
  const availability = [];
  const controller = new EmotionVideoController({ stage, neutral, clip,
    blackout: { classList: classes() }, reducedMotion: { matches: false },
    onStateChange: state => states.push(state), onReceipt: receipt => receipts.push(receipt),
    onAck: ack => acks.push(ack), onAvailabilityChange: enabled => availability.push(enabled) });

  await controller.start();
  controller.setCallId('call-1');
  assert.equal(stage.hidden, false);
  assert.equal(neutral.paused, false);
  assert.equal(availability.at(-1), true);
  assert.equal(controller.acceptEvent({ sequence: 1, state: 'invented' }), false);
  neutral.currentTime = 15;
  assert.equal(controller.acceptEvent({ call_id:'call-1',sequence: 1, state: 'happy' }), true);
  assert.equal(acks.length,0,'loaded video cannot be acknowledged before visible frame');
  await wait(600);
  assert.equal(controller.phase, 'playing');
  assert.equal(clip.src, CLIPS.happy);
  assert.equal(neutral.paused, true);
  assert.equal(controller.neutralTime, 15);
  assert.equal(states.at(-1), 'happy');
  assert.deepEqual(receipts.slice(0,4).map(item=>item.type),
    ['requested','loaded','first_frame_visible','switch_completed']);
  assert.deepEqual(acks[0],{call_id:'call-1',sequence:1,status:'presented'});
  clip.onended();
  await wait(500);
  assert.equal(controller.phase, 'neutral');
  assert.equal(neutral.currentTime, 15);
  assert.equal(neutral.paused, false);
  assert.equal(states.at(-1), 'neutral');
  assert.equal(controller.acceptEvent({ sequence: 1, state: 'sad' }), false);
  controller.setEnabled(false);
  assert.equal(availability.at(-1),false);
  assert.equal(controller.acceptEvent({ call_id:'call-1',sequence: 2, state: 'sad' }), false);
  assert.deepEqual(acks[1],{call_id:'call-1',sequence:2,status:'skipped'});
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
  assert.equal(availability.at(-1),false);

  // Older iPhone Safari builds lack requestVideoFrameCallback. Playback progress
  // plus two paint opportunities is the conservative fallback.
  global.requestAnimationFrame = callback => setTimeout(callback,0);
  const oldSafariClip=video();
  delete oldSafariClip.requestVideoFrameCallback;
  const listeners=new Set();
  oldSafariClip.addEventListener=(name,callback)=>{ if(name==='timeupdate') listeners.add(callback); };
  oldSafariClip.removeEventListener=(_name,callback)=>listeners.delete(callback);
  oldSafariClip.play=function(){
    this.paused=false;
    setTimeout(()=>{ this.currentTime=.04; for(const callback of listeners) callback(); },20);
    return Promise.resolve();
  };
  const safariAcks=[];
  const safari=new EmotionVideoController({stage:{hidden:true,classList:classes()},
    neutral:video(),clip:oldSafariClip,blackout:{classList:classes()},
    reducedMotion:{matches:false},onAck:ack=>safariAcks.push(ack)});
  await safari.start(); safari.setCallId('call-2');
  safari.acceptEvent({call_id:'call-2',sequence:1,state:'sad'});
  await wait(600);
  assert.deepEqual(safariAcks[0],{call_id:'call-2',sequence:1,status:'presented'});
  safari.stop();

  const expiredAcks=[];
  const expired=new EmotionVideoController({stage:{hidden:true,classList:classes()},
    neutral:video(),clip:video(),blackout:{classList:classes()},
    reducedMotion:{matches:false},onAck:ack=>expiredAcks.push(ack)});
  await expired.start(); expired.setCallId('call-expired');
  expired.acceptEvent({call_id:'call-expired',sequence:1,state:'angry',
    issued_at:Date.now()-7900,ttl_ms:8000});
  await wait(300);
  assert.deepEqual(expiredAcks[0],{call_id:'call-expired',sequence:1,status:'skipped'},
    'expired during fade must not present the stale video');
  assert.equal(expired.phase,'neutral');
  expired.stop();

  const pendingAcks=[];
  const slow=video(); slow.readyState=0;
  const interrupted=new EmotionVideoController({stage:{hidden:true,classList:classes()},
    neutral:video(),clip:slow,blackout:{classList:classes()},
    reducedMotion:{matches:false},onAck:ack=>pendingAcks.push(ack)});
  await interrupted.start(); interrupted.setCallId('call-3');
  interrupted.acceptEvent({call_id:'call-3',sequence:1,state:'happy'});
  interrupted.stop();
  assert.deepEqual(pendingAcks[0],{call_id:'call-3',sequence:1,status:'skipped'},
    'hangup must acknowledge before callId is cleared');
  console.log('emotion video state transitions passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
