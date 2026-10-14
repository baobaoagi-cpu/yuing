const assert = require('node:assert/strict');
const {decodeAgentSketchEvent,LiveSketchController} = require('./live-sketch.js');

class FakeElement {
  constructor(name) { this.name=name; this.children=[]; this.style={}; this.isConnected=false; }
  setAttribute(name,value) { this[name]=value; }
  appendChild(child) { this.children.push(child); child.isConnected=true; return child; }
  querySelectorAll(name) { return this.children.flatMap(child =>
    [...(child.name===name?[child]:[]),...child.querySelectorAll(name)]); }
  querySelector(name) { return this.querySelectorAll(name)[0] || null; }
  getTotalLength() { return this.name==='path'?100:0; }
  animate(frames,options) { this.animation={frames,options}; }
  replaceChildren() { for(const child of this.children) child.isConnected=false; this.children=[]; }
  remove() { this.isConnected=false; }
}
global.document={hidden:false,createElementNS:(_ns,name)=>new FakeElement(name),
  createElement:name=>new FakeElement(name)};
global.innerWidth=390;
global.innerHeight=844;

async function main(){
  const agent={identity:'agent-1',sid:'a',kind:'agent'};
  const room={remoteParticipants:new Map([['agent-1',agent]])};
  const base={call_id:'call-1',sequence:1,issued_at:1000,ttl_ms:5000,
    kind:'sun',anchor:{x:.25,y:.4},size:.18,color:'#ffcc44',duration_ms:8000};
  const packet=fields=>new TextEncoder().encode(JSON.stringify({...base,...fields}));
  const decode=(fields={},options={})=>decodeAgentSketchEvent({
    payload:packet(fields),participant:agent,topic:'dunni.sketch.v1',room,
    callId:'call-1',isAgent:p=>p.kind==='agent',now:2000,...options
  });
  assert.equal(decode().kind,'sun');
  assert.equal(decode({kind:'text',text:'ABC'}).text,'ABC');
  assert.equal(decode({kind:'strokes',strokes:[[[0,0],[1,1]]]}).kind,'strokes');
  assert.equal(decode({call_id:'other'}),null);
  assert.equal(decode({issued_at:-9000}),null);
  assert.equal(decode({anchor:{x:2,y:0}}),null);
  assert.equal(decode({color:'url(javascript:alert(1))'}),null);
  assert.equal(decode({kind:'text',text:'<script>'}).kind,'text');
  assert.equal(decode({kind:'text',text:'\n'}),null);
  assert.equal(decode({kind:'strokes',strokes:[[[0,0],[2,0]]]}),null);
  assert.equal(decode({}, {topic:'other'}),null);
  assert.equal(decode({}, {participant:{identity:'stranger'}}),null);
  assert.equal(decode({}, {isAgent:()=>false}),null);

  const layer=new FakeElement('layer'); layer.isConnected=true;
  layer.clientWidth=390; layer.clientHeight=844;
  const callbacks=[]; const receipts=[];
  const controller=new LiveSketchController({layer,reducedMotion:{matches:true},
    now:()=>2000,raf:callback=>callbacks.push(callback),onAck:receipt=>receipts.push(receipt)});
  assert.equal(await controller.acceptEvent(decode()),false,'no call cannot draw');
  controller.start('call-1');
  const drawing=controller.acceptEvent(decode());
  assert.equal(receipts.length,0,'cannot acknowledge before paint');
  assert.equal(layer.children.length,1);
  callbacks.shift()(); await Promise.resolve();
  assert.equal(receipts.length,0,'one RAF is not enough');
  callbacks.shift()(); assert.equal(await drawing,true);
  assert.deepEqual(receipts[0],{call_id:'call-1',sequence:1,status:'rendered'});
  assert.equal(await controller.acceptEvent(decode()),false,'replayed sequence cannot draw');
  controller.setEnabled(false);
  assert.equal(layer.children.length,0,'disabling clears drawings');
  assert.equal(await controller.acceptEvent(decode({sequence:2})),false);
  assert.equal(receipts.at(-1).status,'skipped');
  controller.setEnabled(true);
  controller.getAvoidRects=()=>[{left:0,top:0,right:390,bottom:844}];
  assert.equal(await controller.acceptEvent(decode({sequence:3})),false);
  assert.equal(receipts.at(-1).status,'skipped');
  controller.getAvoidRects=()=>[{left:0,top:0,right:130,bottom:190}];
  assert.equal(controller.place(decode({anchor:{x:.05,y:.12}})),null,
    'unsafe top-left anchor must not be moved elsewhere');
  layer.clientWidth=844; layer.clientHeight=390;
  assert.equal(controller.place(decode({anchor:{x:.05,y:.12}})),null,
    'landscape must retain top-left safety zone');
  controller.getAvoidRects=()=>[];
  const landscape=controller.place(decode({anchor:{x:.75,y:.5}}));
  assert.ok(landscape && landscape.x>500 && landscape.size<100,
    'landscape drawing uses short viewport side and requested region');
  controller.stop();
  assert.equal(layer.children.length,0,'hangup clears drawings');
  const moving=new LiveSketchController({layer,reducedMotion:{matches:false},
    now:()=>2000,raf:callback=>callbacks.push(callback),onAck:receipt=>receipts.push(receipt)});
  moving.start('call-1');
  const inProgress=moving.acceptEvent(decode());
  const path=layer.children[0].querySelector('path');
  assert.equal(path.style.strokeDashoffset,'94','first path starts partly visible');
  assert.equal(receipts.length,3,'no new ACK before painting');
  callbacks.shift()(); await Promise.resolve(); callbacks.shift()();
  assert.equal(await inProgress,true);
  assert.ok(path.animation,'path animates as a stroke, not just a faded container');
  moving.stop();
  console.log('Live sketch event validation, paint ACK, disable and safety: PASS');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
