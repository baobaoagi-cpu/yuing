const $=id=>document.getElementById(id);
const history=[];
let ready=false,busy=false;
function status(message){$('status').textContent=message;}
function bubble(role,text,sources=[]){
  const article=document.createElement('article');article.className=role;
  const who=document.createElement('span');who.textContent=role==='user'?'你':'端倪・測試版';
  const body=document.createElement('p');body.textContent=text;article.append(who,body);
  if(sources.length){const note=document.createElement('div');note.className='sources';note.textContent=`測試資料命中：${sources.map(s=>s.kind).join('、')}`;article.append(note);}
  $('dialogue').append(article);$('dialogue').scrollTop=$('dialogue').scrollHeight;
}
async function refresh(){
  try{const response=await fetch('/api/status',{cache:'no-store'});const data=await response.json();
    ready=!!data.ready;status(ready?'本機模型與零工具設定已確認；可開始測試。':`本機服務未就緒：${JSON.stringify(data.checks||data.error)}`);
  }catch(error){ready=false;status(`無法確認本機服務：${error.message}`);}
  $('send').disabled=!ready;$('record').disabled=!ready;
}
async function send(text){
  text=text.trim();if(!text||busy||!ready)return;
  busy=true;$('send').disabled=true;$('record').disabled=true;bubble('user',text);status('正在以本機模型回覆…');
  try{const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,history})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'本機回覆失敗');
    bubble('assistant',data.reply,data.sources||[]);history.push({role:'user',content:text},{role:'assistant',content:data.reply});
    if(history.length>8)history.splice(0,history.length-8);status(`本機回覆完成；測試記憶 ${data.memory_hits} 筆、測試知識 ${data.knowledge_hits} 筆。`);
    if($('speak').checked&&'speechSynthesis' in window){speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(data.reply);utterance.lang='zh-TW';speechSynthesis.speak(utterance);}
  }catch(error){bubble('assistant',`本機回覆未完成：${error.message}`);status('本機回覆未完成。');}
  finally{busy=false;$('send').disabled=!ready;$('record').disabled=!ready;}
}
$('form').addEventListener('submit',event=>{event.preventDefault();const text=$('message').value;$('message').value='';send(text);});
function wav(samples,sampleRate){
  const data=new ArrayBuffer(44+samples.length*2),view=new DataView(data);
  const write=(offset,str)=>{for(let i=0;i<str.length;i++)view.setUint8(offset+i,str.charCodeAt(i));};
  write(0,'RIFF');view.setUint32(4,36+samples.length*2,true);write(8,'WAVE');write(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);
  view.setUint16(32,2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,samples.length*2,true);
  samples.forEach((x,i)=>view.setInt16(44+i*2,Math.round(Math.max(-1,Math.min(1,x))*32767),true));
  return data;
}
$('record').addEventListener('click',async()=>{
  if(busy||!ready)return;busy=true;$('record').disabled=true;$('send').disabled=true;
  let stream,context,processor,source,sink;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true});context=new AudioContext();await context.resume();
    source=context.createMediaStreamSource(stream);processor=context.createScriptProcessor(4096,1,1);
    sink=context.createGain();sink.gain.value=0;source.connect(processor);processor.connect(sink);sink.connect(context.destination);
    const chunks=[];processor.onaudioprocess=e=>chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    status('正在錄音 6 秒…');await new Promise(resolve=>setTimeout(resolve,6000));
    processor.disconnect();source.disconnect();sink.disconnect();
    const length=chunks.reduce((n,c)=>n+c.length,0),samples=new Float32Array(length);
    let offset=0;chunks.forEach(c=>{samples.set(c,offset);offset+=c.length;});
    status('正在本機辨識語音…');const response=await fetch('/api/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav(samples,context.sampleRate)});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'語音辨識失敗');
    if(data.text){$('message').value=data.text;status('語音已轉成文字；請檢查後按送出。');}
    else status('沒有辨識到可用的語音；請重試或打字。');
  }catch(error){status(`錄音未完成：${error.message}`);}
  finally{if(processor)processor.disconnect();if(source)source.disconnect();if(sink)sink.disconnect();if(stream)stream.getTracks().forEach(t=>t.stop());if(context)await context.close().catch(()=>{});busy=false;$('record').disabled=!ready;$('send').disabled=!ready;}
});
refresh();
