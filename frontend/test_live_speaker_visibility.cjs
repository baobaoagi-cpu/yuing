// Run with Node: node frontend/test_live_speaker_visibility.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const begin = html.indexOf('  function setLiveSpeakerVisible(role,visible){');
const end = html.indexOf('  const meetingView=', begin);
assert(begin >= 0 && end > begin, 'speaker activity implementation must exist');

function element(){
  const classes = new Set();
  return {
    classes, attrs:{}, textContent:'', scrollTop:0, scrollHeight:0,
    classList:{toggle(name,on){if(on) classes.add(name); else classes.delete(name);},
      remove(name){classes.delete(name);}},
    setAttribute(name,value){this.attrs[name]=value;},
  };
}
const turns={assistant:element(),user:element()};
const avatars={assistant:element(),user:element()};
const texts={assistant:element(),user:element()};
const bubbles={assistant:element(),user:element()};
const source=html.slice(begin,end);
const runtime=new Function('liveTurns','liveAvatars','liveTexts','liveBubbles',`
  const liveVisible={assistant:false,user:false};
  const liveDisplayedSegment={assistant:'',user:''};
  const liveSegmentAtHide={assistant:'',user:''};
  const liveSegments=new Map(),liveLatest={assistant:0,user:0};
  let liveSequence=0;
  let room=null,muted=false,callPhase='idle',lastUserVoiceAt=0,lastBaoVoiceAt=0;
  ${source}
  return {syncLiveSpeakerVisibility,showLiveTranscript,resetLiveDialogue,
    state:{set room(v){room=v;},
    set muted(v){muted=v;}, set callPhase(v){callPhase=v;},
    set lastUserVoiceAt(v){lastUserVoiceAt=v;}, set lastBaoVoiceAt(v){lastBaoVoiceAt=v;}}};
`)(turns,avatars,texts,bubbles);
const shown=role=>turns[role].classes.has('speaking-visible') &&
  avatars[role].classes.has('speaking-visible');

runtime.syncLiveSpeakerVisibility(1000);
assert.equal(shown('assistant'),false,'standby hides Dunni');
assert.equal(shown('user'),false,'standby hides the user');

runtime.state.room={}; runtime.state.callPhase='listening';
runtime.state.lastUserVoiceAt=1050;
runtime.syncLiveSpeakerVisibility(1100);
assert.equal(shown('user'),true,'local audio reveals the user');
assert.equal(shown('assistant'),false,'local audio must not reveal Dunni');
assert.equal(turns.user.attrs['aria-hidden'],'false');
runtime.showLiveTranscript('user','first','旧句',true);

runtime.state.lastBaoVoiceAt=1150;
runtime.syncLiveSpeakerVisibility(1200);
assert.equal(shown('assistant'),true,'remote audio reveals Dunni');
assert.equal(shown('user'),true,'simultaneous speech can show both');

runtime.syncLiveSpeakerVisibility(2600);
assert.equal(shown('user'),false,'silence hides the user after the grace period');
assert.equal(shown('assistant'),false,'silence hides Dunni after the grace period');
assert.equal(texts.user.textContent,'','hidden caption does not linger');

runtime.showLiveTranscript('user','first','旧句（晚到）',true);
assert.equal(texts.user.textContent,'旧句（晚到）');
runtime.state.lastUserVoiceAt=2700;
runtime.syncLiveSpeakerVisibility(2750);
assert.equal(shown('user'),true);
assert.equal(texts.user.textContent,'','next speech cannot flash the previous segment');
runtime.showLiveTranscript('user','second','新句',false);
assert.equal(texts.user.textContent,'新句','new segment appears while speaking');

runtime.state.lastBaoVoiceAt=2800;
runtime.syncLiveSpeakerVisibility(4300);
runtime.showLiveTranscript('assistant','agent-new','端倪新句',false);
runtime.state.lastBaoVoiceAt=4400;
runtime.syncLiveSpeakerVisibility(4450);
assert.equal(texts.assistant.textContent,'端倪新句',
  'new transcript arriving before audible audio is retained');

runtime.state.muted=true;
runtime.syncLiveSpeakerVisibility(4450);
assert.equal(shown('user'),false,'muted microphone cannot reveal user bubble');
runtime.state.muted=false;
runtime.state.callPhase='reconnecting';
runtime.syncLiveSpeakerVisibility(4500);
assert.equal(shown('assistant'),false,'reconnecting hides stale remote activity');
runtime.state.callPhase='listening'; runtime.state.room=null;
runtime.syncLiveSpeakerVisibility(4550);
assert.equal(shown('assistant'),false,'ended call hides remote activity immediately');
console.log('Live speaker bubbles: audio-driven visibility, silence, mute, disconnect PASS');
