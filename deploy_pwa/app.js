const $ = (id) => document.getElementById(id);
const ui = {button:$('listen-button'),note:$('button-note'),stage:$('sound-stage'),title:$('stage-title'),subtitle:$('stage-subtitle'),badge:$('live-badge'),events:$('event-list'),file:$('audio-file')};

// The first classifier is deliberately independent from DUANNI HOME.
// Replace classifyWaveform() when the owner's sound-sensing implementation is available.
const GROUPS = [
  {id:'sneeze',title:'疑似打噴嚏',icon:'✳',labels:['Sneeze'],reply:'剛剛好像有一聲噴嚏。還好嗎？'},
  {id:'cough',title:'疑似咳嗽',icon:'◌',labels:['Cough'],reply:'我好像聽到你咳了一下。要不要先喝點水？'},
  {id:'cry',title:'疑似哭聲',icon:'◍',labels:['Baby cry, infant cry','Crying, sobbing'],reply:'我好像聽到哭聲。你需要我陪你一下嗎？'},
  {id:'car',title:'疑似車聲',icon:'↗',labels:['Car passing by','Traffic noise, roadway noise','Car','Motor vehicle (road)'],reply:'旁邊好像有車聲，先留意周圍。'},
  {id:'impact',title:'疑似碰撞聲',icon:'◆',labels:['Thump, thud','Bang','Slap, smack','Crash'],reply:'剛剛有一聲比較大的聲響，還好嗎？'},
  {id:'music',title:'疑似音樂',icon:'♫',labels:['Music','Background music','Musical instrument'],reply:'我好像聽見音樂了。你正在聽什麼？'}
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
  ui.badge.textContent=mode==='listening'?'聆聽中':mode==='busy'?'處理中':'待機';
  ui.badge.classList.toggle('on',mode==='listening');
}

async function loadModel(){
  if(session) return session;
  if(!window.ort) throw new Error('聲音推論程式未載入。');
  setStage('正在準備聲音模型','第一次開啟需載入本機附帶的模型。','busy');
  ort.env.wasm.numThreads=1;
  ort.env.wasm.wasmPaths=new URL('./assets/',location.href).href;
  const [mapResponse,model] = await Promise.all([
    fetch('./assets/yamnet_class_map.csv'),
    ort.InferenceSession.create('./assets/yamnet.onnx',{executionProviders:['wasm']})
  ]);
  if(!mapResponse.ok) throw new Error('聲音類別表載入失敗。');
  const csv=await mapResponse.text();
  labels=csv.trim().split(/\r?\n/).slice(1).map(line=>{
    const match=line.match(/^\d+,[^,]+,(.*)$/);
    return match ? match[1].replace(/^"|"$/g,'').replace(/""/g,'"') : '';
  });
  if(labels.length!==521) throw new Error('聲音類別表不完整。');
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
  if(sourceLabel==='即時麥克風' && now-(lastShown.get(key)||0)<9000) return;
  lastShown.set(key,now);
  const title=observed?candidate.title:'尚未辨識出指定事件';
  const detail=observed?`模型候選：${candidate.label} · 原始分數 ${candidate.score.toFixed(2)}`:`最高候選：${result.top.label||'未知'} · 原始分數 ${result.top.score.toFixed(2)}`;
  const reply=observed && candidate.score>=RESPOND_SCORE?candidate.reply:null;
  if(ui.events.querySelector('.empty-state')) ui.events.replaceChildren();
  const article=document.createElement('article');article.className='event';
  const symbol=document.createElement('div');symbol.className='event-symbol';symbol.textContent=observed?candidate.icon:'·';
  const body=document.createElement('div');
  const h=document.createElement('h3');h.textContent=title;
  const p=document.createElement('p');p.textContent=detail;
  body.append(h,p);
  if(reply){const small=document.createElement('small');small.textContent=`預寫回應：「${reply}」`;body.append(small);}
  const meta=document.createElement('div');meta.className='event-meta';
  const strong=document.createElement('strong');strong.textContent=observed?candidate.score.toFixed(2):'—';
  const time=document.createElement('span');time.textContent=`${new Date(now).toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit'})} · ${sourceLabel}`;
  meta.append(strong,time);article.append(symbol,body,meta);
  ui.events.prepend(article);
  while(ui.events.children.length>8) ui.events.lastElementChild.remove();
  if(observed) setStage(title,reply||'聲音事件候選，分數不足以觸發回應。',running?'listening':'idle');
  else setStage(running?'正在聆聽生活聲':'分析完成',observed?'':`目前最高候選：${result.top.label||'未知'}`,running?'listening':'idle');
}

async function analyze(wave,sourceLabel){
  if(busy) return;
  busy=true;
  try{const result=await classifyWaveform(wave);addResult(result,sourceLabel);}
  catch(error){setStage('辨識未完成',error.message||'無法分析聲音。');}
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
  ui.button.textContent='開始聆聽';
  ui.note.textContent='麥克風已關閉。';
  setStage('聆聽已結束','按下開始，可再次測試。');
}

async function startListening(){
  if(!navigator.mediaDevices?.getUserMedia) throw new Error('此頁需要 HTTPS 才能取得麥克風。');
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
      analyze(wave,'即時麥克風');
    }
  };
  ui.button.textContent='停止聆聽';
  ui.note.textContent='正在使用麥克風 · 離開頁面或按停止即關閉';
  setStage('正在聆聽生活聲','聲音在此裝置上分析；高把握度事件才顯示預寫回應。','listening');
}

ui.button.addEventListener('click',async()=>{
  if(running){stopListening();return;}
  ui.button.disabled=true;
  try{await startListening();}
  catch(error){
    stopListening();
    const detail=error.name==='NotAllowedError'?'請在 Safari 允許此網站使用麥克風。':
      error.name==='NotFoundError'?'找不到可用的麥克風。':
      error.message||'無法開啟麥克風。';
    setStage('尚未開始聆聽',detail);
  }
  finally{ui.button.disabled=false;}
});

ui.file.addEventListener('change',async()=>{
  const file=ui.file.files?.[0];if(!file)return;
  setStage('正在分析音檔',file.name,'busy');
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
    if(wave.length<16000) throw new Error('音檔需至少 1 秒。');
    await analyze(wave,'音檔');
  }catch(error){setStage('音檔分析失敗',error.message||'無法讀取此音檔。');}
  finally{if(context) await context.close().catch(()=>{});ui.file.value='';}
});

document.addEventListener('visibilitychange',()=>{if(document.hidden && running) stopListening();});
window.addEventListener('pagehide',()=>{if(running) stopListening();});
if('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(()=>{});

// These controls fail closed. The server advertises only capabilities backed by
// a real endpoint; an unavailable feature stays visible but cannot be used.
const assistantUi = {
  badge:$('connection-badge'), note:$('connection-note'), strip:$('capability-strip'),
  log:$('conversation'), form:$('chat-form'), input:$('chat-input'), send:$('send-button'),
  voice:$('voice-button'), image:$('image-input'), imageLabel:$('image-label'),
  document:$('document-input'), documentLabel:$('document-label'), status:$('action-status'),
  latency:$('latency-button'), latencyResult:$('latency-result')
};
const CAP_NAMES={text:'文字',turn_voice:'回合語音',live_voice:'即時通話',image:'圖片',files:'檔案'};
let activeCaps={};
let voiceCapture=null;

function setActionStatus(value){assistantUi.status.textContent=value;}
function renderCapabilities(caps){
  activeCaps=caps;
  for(const [key,label] of Object.entries(CAP_NAMES)){
    const item=assistantUi.strip.querySelector(`[data-capability="${key}"]`);
    if(!item)continue;
    const on=caps[key]===true;
    item.textContent=`${label} · ${on?'可測試':'待接通'}`;
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
  assistantUi.note.textContent=textReady?'能力來自目前伺服器的即時檢查；語音轉文字後會先讓你確認，再送出對話。':caps.files===true?'目前可讀取 PDF、DOCX、PPTX 的文字；對話和圖片仍待接通。下方也可測試生活聲辨識。':'目前可繼續測試下方的生活聲辨識；對話、圖片與檔案入口會在接通後開放。';
  setActionStatus(textReady?'文字對話已接通。':caps.files===true?'檔案文字抽取可測；對話服務尚未接通。':'對話服務尚未接通。');
}
async function refreshCapabilities(){
  try{
    const response=await fetch('./api/capabilities',{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(!body||typeof body!=='object')throw new Error('能力回應無效');
    renderCapabilities(body);
  }catch(error){renderCapabilities({});assistantUi.note.textContent=`能力檢查失敗：${error.message}。可測試下方的生活聲辨識。`;}
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
  assistantUi.send.disabled=true;assistantUi.input.disabled=true;setActionStatus('正在等待端倪回應…');
  try{
    const response=await fetch('./api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(typeof body.reply!=='string'||!body.reply.trim())throw new Error('回應內容無效');
    addMessage('user',message);addMessage('assistant',body.reply);assistantUi.input.value='';setActionStatus('對話完成。');
  }catch(error){setActionStatus(`對話未完成：${error.message}`);await refreshCapabilities();}
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
  assistantUi.voice.textContent='錄一段話';return capture;
}
async function finishVoiceCapture(){
  const capture=closeVoiceCapture();if(!capture)return;
  if(capture.samples.length<16000){setActionStatus('請至少錄 1 秒，再試一次。');return;}
  assistantUi.voice.disabled=true;setActionStatus('正在將語音轉成文字…');
  try{
    const wave=wavFromSamples(capture.samples,16000);
    const response=await fetch('./api/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wave});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(typeof body.text!=='string'||!body.text.trim())throw new Error('未辨識出語句');
    assistantUi.input.value=body.text.trim();assistantUi.input.focus();setActionStatus('語音已轉成文字。確認內容後，按「送出」。');
  }catch(error){setActionStatus(`語音未完成：${error.message}`);await refreshCapabilities();}
  finally{assistantUi.voice.disabled=activeCaps.turn_voice!==true;}
}
assistantUi.voice.addEventListener('click',async()=>{
  if(voiceCapture){await finishVoiceCapture();return;}
  if(activeCaps.turn_voice!==true)return;
  if(running)stopListening();
  if(!navigator.mediaDevices?.getUserMedia){setActionStatus('此頁需要 HTTPS 才能錄音。');return;}
  assistantUi.voice.disabled=true;
  try{
    const mic=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
    const context=new (window.AudioContext||window.webkitAudioContext)();await context.resume();
    const sourceNode=context.createMediaStreamSource(mic),processorNode=context.createScriptProcessor(4096,1,1),silent=context.createGain();silent.gain.value=0;
    const samples=[];
    sourceNode.connect(processorNode);processorNode.connect(silent);silent.connect(context.destination);
    processorNode.onaudioprocess=event=>{if(voiceCapture){for(const sample of resample(event.inputBuffer.getChannelData(0),context.sampleRate))samples.push(sample);}};
    voiceCapture={stream:mic,context,source:sourceNode,processor:processorNode,silent,samples};
    assistantUi.voice.textContent='停止並轉文字';setActionStatus('正在錄音，最多 8 秒。');
    setTimeout(()=>{if(voiceCapture?.stream===mic)finishVoiceCapture();},8000);
  }catch(error){setActionStatus(error.name==='NotAllowedError'?'請允許此網站使用麥克風。':`錄音失敗：${error.message}`);}
  finally{assistantUi.voice.disabled=false;}
});

async function analyzeUpload(file,kind){
  if(!file||activeCaps[kind]!==true)return;
  const input=kind==='image'?assistantUi.image:assistantUi.document;
  const limit=kind==='image'?4_000_000:5_000_000;
  if(file.size>limit){setActionStatus(`檔案超過 ${kind==='image'?'4':'5'} MB，請選擇較小的檔案。`);input.value='';return;}
  input.disabled=true;setActionStatus(`正在分析${kind==='image'?'圖片':'檔案'}：${file.name}`);
  try{
    const data=new FormData();data.append('file',file,file.name);
    const response=await fetch('./api/analyze',{method:'POST',body:data});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if(typeof body.summary!=='string'||!body.summary.trim())throw new Error('分析結果無效');
    addMessage('user',`請看${kind==='image'?'圖片':'檔案'}：${file.name}`);addMessage('assistant',body.summary);
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
    try{const response=await fetch(path,{cache:'no-store'});checks.push(`${path}: ${response.ok?'成功':'失敗 HTTP '+response.status} · ${Math.round(performance.now()-started)} ms`);}
    catch(error){checks.push(`${path}: 連線失敗 · ${Math.round(performance.now()-started)} ms`);}
  }
  const time=new Date().toLocaleString('zh-TW',{hour12:false});
  assistantUi.latencyResult.textContent=`${time}（本裝置）｜${checks.join('｜')}`;
  assistantUi.latency.disabled=false;
});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&voiceCapture)closeVoiceCapture();});
window.addEventListener('pagehide',()=>{if(voiceCapture)closeVoiceCapture();});
refreshCapabilities();
