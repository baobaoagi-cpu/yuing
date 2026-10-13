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
