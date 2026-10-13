const timeForm = document.getElementById('time-form');
const timeInput = document.getElementById('time');
const source = document.getElementById('source');
const target = document.getElementById('target');
const result = document.getElementById('result');
const convert = document.getElementById('convert');

function show(message) { result.textContent = message; result.hidden = false; }
function fill(select, zones) {
  select.replaceChildren();
  for (const [name, label] of Object.entries(zones)) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = `${label} · ${name}`;
    select.append(option);
  }
}
fetch('/api/v2/tools/time', {credentials:'same-origin', cache:'no-store'})
  .then(async response => { if (!response.ok) throw new Error(); return response.json(); })
  .then(data => { fill(source, data.zones); fill(target, data.zones); source.value = 'Asia/Shanghai'; target.value = 'America/New_York'; })
  .catch(() => { convert.disabled = true; show('工具暂时无法使用，请稍后重试。'); });

timeForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (convert.disabled) return;
  convert.disabled = true;
  show('正在换算…');
  try {
    const response = await fetch('/api/v2/tools/time', {
      method:'POST', credentials:'same-origin', cache:'no-store',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({time:timeInput.value,source_timezone:source.value,target_timezone:target.value})
    });
    const data = await response.json();
    if (!response.ok) { show(data.error || '换算失败，请重试。'); return; }
    const item = data.result;
    show(`${item.source_timezone}：${item.source}\n${item.target_timezone}：${item.target}`);
  } catch { show('换算失败，请检查连接后重试。'); }
  finally { convert.disabled = false; }
});
