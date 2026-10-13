const $ = (id) => document.getElementById(id);
const ui = {button:$('listen-button'),note:$('button-note'),stage:$('sound-stage'),title:$('stage-title'),subtitle:$('stage-subtitle'),badge:$('live-badge'),events:$('event-list'),file:$('audio-file')};

// The first classifier is deliberately independent from DUANNI HOME.
// Replace classifyWaveform() when the owner's sound-sensing implementation is available.
const GROUPS = [
  {id:'sneeze',title:'疑似打喷嚏',icon:'✳',labels:['Sneeze'],reply:'刚刚好像有一声喷嚏。还好吗？'},
  {id:'cough',title:'疑似咳嗽',icon:'◌',labels:['Cough'],reply:'我好像听到你咳了一下。要不要先喝点水？'},
  {id:'cry',title:'疑似哭声',icon:'◍',labels:['Baby cry, infant cry','Crying, sobbing'],reply:'我好像听到哭声。你需要我陪你一下吗？'},
  {id:'car',title:'疑似车声',icon:'↗',labels:['Car passing by','Traffic noise, roadway noise','Car','Motor vehicle (road)'],reply:'旁边好像有车声，先留意周围。'},
  {id:'impact',title:'疑似碰撞声',icon:'◆',labels:['Thump, thud','Bang','Slap, smack','Crash'],reply:'刚刚有一声比较大的声响，还好吗？'},
  {id:'music',title:'疑似音乐',icon:'♫',labels:['Music','Background music','Musical instrument'],reply:'我好像听见音乐了。你正在听什么？'}
];
const RESPOND_SCORE = .48;
const SHOW_SCORE = .18;
let session = null;
let labels = [];
let stream = null;
let audioContext = null;
let source = null;
let processor = null;
let silentOutput = null;
let running = false;
let busy = false;
let pcm = [];
let lastAnalysisAt = 0;
const lastShown = new Map();

function setStage(title, subtitle, mode='idle') {
  ui.title.textContent=title;
  ui.subtitle.textContent=subtitle;
  ui.stage.classList.toggle('listening',mode==='listening');
  ui.badge.textContent=mode==='listening'?'聆听中':mode==='busy'?'处理中':'待机';
  ui.badge.classList.toggle('on',mode==='listening');
}

async function loadModel(){
  if(session) return session;
  if(!window.ort) throw new Error('声音推论程式未载入。');
  setStage('正在准备声音模型','第一次开启需载入本机附带的模型。','busy');
  ort.env.wasm.numThreads=1;
  ort.env.wasm.wasmPaths=new URL('./assets/',location.href).href;
  const [mapResponse,model] = await Promise.all([
    fetch('./assets/yamnet_class_map.csv'),
    ort.InferenceSession.create('./assets/yamnet.onnx',{executionProviders:['wasm']})
  ]);
  if(!mapResponse.ok) throw new Error('声音类别表载入失败。');
  const csv=await mapResponse.text();
  labels=csv.trim().split(/\r?\n/).slice(1).map(line=>{
    const match=line.match(/^\d+,[^,]+,(.*)$/);
    return match ? match[1].replace(/^"|"$/g,'').replace(/""/g,'"') : '';
  });
  if(labels.length!==521) throw new Error('声音类别表不完整。');
  session=model;
  return session;
}

function resample(input,fromRate,toRate=16000){
  if(fromRate===toRate) return new Float32Array(input);
  const size=Math.floor(input.length*toRate/fromRate);
  const output=new Float32Array(size);
  const ratio=fromRate/toRate;
  for(let i=0;i<size;i++){
    const pos=i*ratio, a=Math.floor(pos),b=Math.min(input.length-1,a+1),f=pos-a;
    output[i]=input[a]*(1-f)+input[b]*f;
  }
  return output;
}

async function classifyWaveform(waveform){
  const model=await loadModel();
  const wave=new Float32Array(waveform);
  const outputs=await model.run({waveform:new ort.Tensor('float32',wave,[wave.length])});
  const scores=outputs.output_0;
  const frames=scores.dims[0];
  const mean=new Float32Array(labels.length);
  const peak=new Float32Array(labels.length);
  for(let f=0;f<frames;f++) for(let i=0;i<labels.length;i++){
    const value=scores.data[f*labels.length+i];
    mean[i]+=value/frames;
    if(value>peak[i]) peak[i]=value;
  }
  const groupResults=GROUPS.map(group=>{
    let best={score:0,label:''};
    for(const label of group.labels){
      const index=labels.indexOf(label);
      if(index>=0 && peak[index]>best.score) best={score:peak[index],label};
    }
    return {...group,...best};
  }).sort((a,b)=>b.score-a.score);
  let top={score:0,label:''};
  mean.forEach((score,index)=>{if(score>top.score) top={score,label:labels[index]};});
  return {groups:groupResults,top,frames};
}

function addResult(result,sourceLabel){
  const candidate=result.groups[0];
  const observed=candidate && candidate.score>=SHOW_SCORE;
  const key=observed?candidate.id:'other';
  const now=Date.now();
  if(sourceLabel==='实时麦克风' && now-(lastShown.get(key)||0)<9000) return;
  lastShown.set(key,now);
  const title=observed?candidate.title:'尚未辨识出指定事件';
  const detail=observed?`模型候选：${candidate.label} · 原始分数 ${candidate.score.toFixed(2)}`:`最高候选：${result.top.label||'未知'} · 原始分数 ${result.top.score.toFixed(2)}`;
  const reply=observed && candidate.score>=RESPOND_SCORE?candidate.reply:null;
  if(ui.events.querySelector('.empty-state')) ui.events.replaceChildren();
  const article=document.createElement('article');article.className='event';
  const symbol=document.createElement('div');symbol.className='event-symbol';symbol.textContent=observed?candidate.icon:'·';
  const body=document.createElement('div');
  const h=document.createElement('h3');h.textContent=title;
  const p=document.createElement('p');p.textContent=detail;
  body.append(h,p);
  if(reply){const small=document.createElement('small');small.textContent=`预写回应：「${reply}」`;body.append(small);}
  const meta=document.createElement('div');meta.className='event-meta';
  const strong=document.createElement('strong');strong.textContent=observed?candidate.score.toFixed(2):'—';
  const time=document.createElement('span');time.textContent=`${new Date(now).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})} · ${sourceLabel}`;
  meta.append(strong,time);article.append(symbol,body,meta);
  ui.events.prepend(article);
  while(ui.events.children.length>8) ui.events.lastElementChild.remove();
  if(observed) setStage(title,reply||'声音事件候选，分数不足以触发回应。',running?'listening':'idle');
  else setStage(running?'正在聆听生活声':'分析完成',observed?'':`目前最高候选：${result.top.label||'未知'}`,running?'listening':'idle');
}

async function analyze(wave,sourceLabel){
  if(busy) return;
  busy=true;
  try{const result=await classifyWaveform(wave);addResult(result,sourceLabel);}
  catch(error){setStage('辨识未完成',error.message||'无法分析声音。');}
  finally{busy=false;}
}

function stopListening(){
  running=false;
  if(processor) processor.disconnect();
  if(source) source.disconnect();
  if(silentOutput) silentOutput.disconnect();
  if(stream) stream.getTracks().forEach(track=>track.stop());
  if(audioContext) audioContext.close().catch(()=>{});
  processor=source=silentOutput=stream=audioContext=null;
  pcm=[];
  ui.button.textContent='开始聆听';
  ui.note.textContent='麦克风已关闭。';
  setStage('聆听已结束','按下开始，可再次测试。');
}

async function startListening(){
  if(!navigator.mediaDevices?.getUserMedia) throw new Error('此页需要 HTTPS 才能取得麦克风。');
  stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:false,autoGainControl:false},video:false});
  audioContext=new (window.AudioContext||window.webkitAudioContext)();
  await audioContext.resume();
  await loadModel();
  source=audioContext.createMediaStreamSource(stream);
  processor=audioContext.createScriptProcessor(4096,1,1);
  silentOutput=audioContext.createGain();silentOutput.gain.value=0;
  source.connect(processor);processor.connect(silentOutput);silentOutput.connect(audioContext.destination);
  pcm=[];running=true;lastAnalysisAt=0;
  processor.onaudioprocess=(event)=>{
    if(!running) return;
    const chunk=resample(event.inputBuffer.getChannelData(0),audioContext.sampleRate);
    for(const value of chunk) pcm.push(value);
    if(pcm.length>64000) pcm.splice(0,pcm.length-64000);
    if(pcm.length>=48000 && Date.now()-lastAnalysisAt>=2500 && !busy){
      lastAnalysisAt=Date.now();
      const wave=Float32Array.from(pcm.slice(-48000));
      analyze(wave,'实时麦克风');
    }
  };
  ui.button.textContent='停止聆听';
  ui.note.textContent='正在使用麦克风 · 离开页面或按停止即关闭';
  setStage('正在聆听生活声','声音在此装置上分析；高把握度事件才显示预写回应。','listening');
}

ui.button.addEventListener('click',async()=>{
  if(running){stopListening();return;}
  ui.button.disabled=true;
  try{await startListening();}
  catch(error){
    stopListening();
    const detail=error.name==='NotAllowedError'?'请在 Safari 允许此网站使用麦克风。':
      error.name==='NotFoundError'?'找不到可用的麦克风。':
      error.message||'无法开启麦克风。';
    setStage('尚未开始聆听',detail);
  }
  finally{ui.button.disabled=false;}
});

ui.file.addEventListener('change',async()=>{
  const file=ui.file.files?.[0];if(!file)return;
  setStage('正在分析音档',file.name,'busy');
  let context=null;
  try{
    context=new (window.AudioContext||window.webkitAudioContext)();
    const decoded=await context.decodeAudioData(await file.arrayBuffer());
    const duration=Math.min(decoded.duration,12);
    const count=Math.floor(duration*decoded.sampleRate);
    const mono=new Float32Array(count);
    for(let channel=0;channel<decoded.numberOfChannels;channel++){
      const samples=decoded.getChannelData(channel);
      for(let i=0;i<count;i++) mono[i]+=samples[i]/decoded.numberOfChannels;
    }
    const wave=resample(mono,decoded.sampleRate);
    if(wave.length<16000) throw new Error('音档需至少 1 秒。');
    await analyze(wave,'音档');
  }catch(error){setStage('音档分析失败',error.message||'无法读取此音档。');}
  finally{if(context) await context.close().catch(()=>{});ui.file.value='';}
});

document.addEventListener('visibilitychange',()=>{if(document.hidden && running) stopListening();});
window.addEventListener('pagehide',()=>{if(running) stopListening();});
if('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(()=>{});

document.querySelector('#logout-button')?.addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch('/api/logout', {method: 'POST', credentials: 'same-origin', cache: 'no-store'});
    if(!response.ok) throw new Error('退出登录请求失败');
    window.location.replace('/login');
  } catch (_) {
    button.disabled = false;
    const status = document.querySelector('#action-status');
    if(status) status.textContent='退出登录未完成，请检查连接后再试。';
  }
});

// These controls fail closed. The server advertises only capabilities backed by
// a real endpoint; an unavailable feature stays visible but cannot be used.
const assistantUi = {
  badge:$('connection-badge'), note:$('connection-note'), strip:$('capability-strip'),
  log:$('conversation'), form:$('chat-form'), input:$('chat-input'), send:$('send-button'),
  voice:$('voice-button'), image:$('image-input'), imageLabel:$('image-label'),
  document:$('document-input'), documentLabel:$('document-label'), status:$('action-status'),
  latency:$('latency-button'), latencyResult:$('latency-result')
};
const CAP_NAMES={text:'文字',turn_voice:'回合语音',live_voice:'实时通话',image:'图片',files:'文件'};
let activeCaps={};
let voiceCapture=null;

function setActionStatus(value){assistantUi.status.textContent=value;}
function renderCapabilities(caps){
  activeCaps=caps;
  for(const [key,label] of Object.entries(CAP_NAMES)){
    const item=assistantUi.strip.querySelector(`[data-capability="${key}"]`);
    if(!item)continue;
    const on=caps[key]===true;
    item.textContent=`${label} · ${on?'可测试':'待接通'}`;
    item.classList.toggle('available',on);
    item.classList.toggle('unavailable',!on);
  }
  const textReady=caps.text===true;
  assistantUi.input.disabled=!textReady;
  assistantUi.send.disabled=!textReady;
  assistantUi.voice.disabled=caps.turn_voice!==true;
  assistantUi.image.disabled=caps.image!==true;
  assistantUi.document.disabled=caps.files!==true;
  assistantUi.imageLabel.classList.toggle('disabled',assistantUi.image.disabled);
  assistantUi.documentLabel.classList.toggle('disabled',assistantUi.document.disabled);
  assistantUi.badge.textContent=Object.values(CAP_NAMES).some((_,i)=>caps[Object.keys(CAP_NAMES)[i]]===true)?'部分接通':'尚未接通';
  assistantUi.badge.classList.toggle('on',textReady);
  assistantUi.note.textContent=textReady?'能力来自目前伺服器的实时检查；语音转文字后会先让你确认，再送出对话。':caps.files===true?'目前可读取 PDF、DOCX、PPTX 的文字；对话和图片仍待接通。下方也可测试生活声辨识。':'目前可继续测试下方的生活声辨识；对话、图片与文件入口会在接通后开放。';
  setActionStatus(textReady?'文字对话已接通。':caps.files===true?'文件文字抽取可测；对话服务尚未接通。':'对话服务尚未接通。');
}
async function refreshCapabilities(){
  try{
    const response=await fetch('./api/capabilities',{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(!body||typeof body!=='object')throw new Error('能力回应无效');
    renderCapabilities(body);
  }catch(error){renderCapabilities({});assistantUi.note.textContent=`能力检查失败：${error.message}。可测试下方的生活声辨识。`;}
}
function addMessage(role,value){
  const empty=assistantUi.log.querySelector('.conversation-empty');if(empty)empty.remove();
  const p=document.createElement('p');p.className=`message ${role}`;p.textContent=value;
  assistantUi.log.append(p);assistantUi.log.scrollTop=assistantUi.log.scrollHeight;
}
assistantUi.form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(activeCaps.text!==true)return;
  const message=assistantUi.input.value.trim();if(!message)return;
  assistantUi.send.disabled=true;assistantUi.input.disabled=true;setActionStatus('正在等待端倪回应…');
  try{
    const response=await fetch('./api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(typeof body.reply!=='string'||!body.reply.trim())throw new Error('回应内容无效');
    addMessage('user',message);addMessage('assistant',body.reply);assistantUi.input.value='';setActionStatus('对话完成。');
  }catch(error){setActionStatus(`对话未完成：${error.message}`);await refreshCapabilities();}
  finally{assistantUi.input.disabled=activeCaps.text!==true;assistantUi.send.disabled=activeCaps.text!==true;}
});

function wavFromSamples(samples,sampleRate){
  const length=samples.length;const bytes=new ArrayBuffer(44+length*2);const view=new DataView(bytes);
  const ascii=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
  ascii(0,'RIFF');view.setUint32(4,36+length*2,true);ascii(8,'WAVE');ascii(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);
  view.setUint16(32,2,true);view.setUint16(34,16,true);ascii(36,'data');view.setUint32(40,length*2,true);
  for(let i=0;i<length;i++){const v=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,v<0?v*32768:v*32767,true);}
  return new Blob([bytes],{type:'audio/wav'});
}
function closeVoiceCapture(){
  if(!voiceCapture)return null;
  const capture=voiceCapture;voiceCapture=null;
  capture.processor.disconnect();capture.source.disconnect();capture.silent.disconnect();
  capture.stream.getTracks().forEach(track=>track.stop());capture.context.close().catch(()=>{});
  assistantUi.voice.textContent='录一段话';return capture;
}
async function finishVoiceCapture(){
  const capture=closeVoiceCapture();if(!capture)return;
  if(capture.samples.length<16000){setActionStatus('请至少录 1 秒，再试一次。');return;}
  assistantUi.voice.disabled=true;setActionStatus('正在将语音转成文字…');
  try{
    const wave=wavFromSamples(capture.samples,16000);
    const response=await fetch('./api/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wave});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(typeof body.text!=='string'||!body.text.trim())throw new Error('未辨识出语句');
    assistantUi.input.value=body.text.trim();assistantUi.input.focus();setActionStatus('语音已转成文字。确认内容后，按「送出」。');
  }catch(error){setActionStatus(`语音未完成：${error.message}`);await refreshCapabilities();}
  finally{assistantUi.voice.disabled=activeCaps.turn_voice!==true;}
}
assistantUi.voice.addEventListener('click',async()=>{
  if(voiceCapture){await finishVoiceCapture();return;}
  if(activeCaps.turn_voice!==true)return;
  if(running)stopListening();
  if(!navigator.mediaDevices?.getUserMedia){setActionStatus('此页需要 HTTPS 才能录音。');return;}
  assistantUi.voice.disabled=true;
  try{
    const mic=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
    const context=new (window.AudioContext||window.webkitAudioContext)();await context.resume();
    const sourceNode=context.createMediaStreamSource(mic),processorNode=context.createScriptProcessor(4096,1,1),silent=context.createGain();silent.gain.value=0;
    const samples=[];
    sourceNode.connect(processorNode);processorNode.connect(silent);silent.connect(context.destination);
    processorNode.onaudioprocess=event=>{if(voiceCapture){for(const sample of resample(event.inputBuffer.getChannelData(0),context.sampleRate))samples.push(sample);}};
    voiceCapture={stream:mic,context,source:sourceNode,processor:processorNode,silent,samples};
    assistantUi.voice.textContent='停止并转文字';setActionStatus('正在录音，最多 8 秒。');
    setTimeout(()=>{if(voiceCapture?.stream===mic)finishVoiceCapture();},8000);
  }catch(error){setActionStatus(error.name==='NotAllowedError'?'请允许此网站使用麦克风。':`录音失败：${error.message}`);}
  finally{assistantUi.voice.disabled=false;}
});

async function analyzeUpload(file,kind){
  if(!file||activeCaps[kind]!==true)return;
  const input=kind==='image'?assistantUi.image:assistantUi.document;
  const limit=kind==='image'?4_000_000:5_000_000;
  if(file.size>limit){setActionStatus(`文件超过 ${kind==='image'?'4':'5'} MB，请选择较小的文件。`);input.value='';return;}
  input.disabled=true;setActionStatus(`正在分析${kind==='image'?'图片':'文件'}：${file.name}`);
  try{
    const data=new FormData();data.append('file',file,file.name);
    const response=await fetch('./api/analyze',{method:'POST',body:data});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(typeof body.summary!=='string'||!body.summary.trim())throw new Error('分析结果无效');
    addMessage('user',`请看${kind==='image'?'图片':'文件'}：${file.name}`);addMessage('assistant',body.summary);
    setActionStatus(body.note||'分析完成。');
  }catch(error){setActionStatus(`分析未完成：${error.message}`);await refreshCapabilities();}
  finally{input.value='';input.disabled=activeCaps[kind]!==true;}
}
assistantUi.image.addEventListener('change',()=>analyzeUpload(assistantUi.image.files?.[0],'image'));
assistantUi.document.addEventListener('change',()=>analyzeUpload(assistantUi.document.files?.[0],'files'));

assistantUi.latency.addEventListener('click',async()=>{
  assistantUi.latency.disabled=true;
  const checks=[];
  for(const path of ['./healthz','./api/capabilities']){
    const started=performance.now();
    try{const response=await fetch(path,{cache:'no-store'});checks.push(`${path}: ${response.ok?'成功':'失败 HTTP '+response.status} · ${Math.round(performance.now()-started)} ms`);}
    catch(error){checks.push(`${path}: 连接失败 · ${Math.round(performance.now()-started)} ms`);}
  }
  const time=new Date().toLocaleString('zh-CN',{hour12:false});
  assistantUi.latencyResult.textContent=`${time}（本装置）｜${checks.join('｜')}`;
  assistantUi.latency.disabled=false;
});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&voiceCapture)closeVoiceCapture();});
window.addEventListener('pagehide',()=>{if(voiceCapture)closeVoiceCapture();});
refreshCapabilities();
