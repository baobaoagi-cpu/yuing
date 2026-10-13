// The authenticated catalog is the source of truth for which tools are usable.
const menuButton = document.getElementById('tool-menu-button');
const menu = document.getElementById('tool-menu');
const content = document.getElementById('tool-menu-content');

function closeToolMenu() {
  menu.hidden = true;
  menuButton.setAttribute('aria-expanded', 'false');
}

function renderToolCatalog(groups) {
  content.replaceChildren();
  for (const group of groups) {
    const section = document.createElement('section');
    section.className = 'tool-group';
    const heading = document.createElement('h3');
    heading.textContent = group.title;
    section.append(heading);
    for (const item of group.items) {
      const ready = item.state === 'ready' && typeof item.href === 'string';
      const row = document.createElement(ready ? 'a' : 'div');
      row.className = `tool-row ${ready ? 'tool-ready' : 'tool-waiting'}`;
      if (ready) {
        row.href = item.href;
        if (item.href === '/chat' && location.pathname === '/chat') {
          row.addEventListener('click', event => {
            event.preventDefault();
            closeToolMenu();
            document.getElementById('message-input')?.focus();
          });
        }
      } else {
        row.setAttribute('aria-disabled', 'true');
      }
      const copy = document.createElement('span');
      const name = document.createElement('strong');
      name.textContent = item.name;
      const detail = document.createElement('small');
      detail.textContent = item.detail;
      copy.append(name, detail);
      const badge = document.createElement('span');
      badge.className = 'tool-badge';
      badge.textContent = ready ? '可使用' : item.state === 'unavailable' ? '暂不可接入' : '待整合';
      row.append(copy, badge);
      section.append(row);
    }
    content.append(section);
  }
}

async function openToolMenu() {
  if (!menu.hidden) { closeToolMenu(); return; }
  for (const id of ['install-panel', 'memory-panel']) {
    const panel = document.getElementById(id);
    if (panel && !panel.hidden) document.getElementById(id.replace('panel', 'close'))?.click();
  }
  menu.hidden = false;
  menuButton.setAttribute('aria-expanded', 'true');
  content.textContent = '正在检查工具状态…';
  try {
    const response = await fetch('/api/v2/tools/catalog', {credentials:'same-origin', cache:'no-store'});
    if (!response.ok) throw new Error();
    const payload = await response.json();
    if (!Array.isArray(payload.groups)) throw new Error();
    renderToolCatalog(payload.groups);
  } catch {
    content.textContent = '暂时无法确认工具状态，请稍后重试。';
  }
}

menuButton.addEventListener('click', openToolMenu);
document.getElementById('tool-menu-close').addEventListener('click', () => {
  closeToolMenu(); menuButton.focus();
});
document.addEventListener('pointerdown', event => {
  if (!menu.hidden && !menu.contains(event.target) && !menuButton.contains(event.target)) closeToolMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !menu.hidden) { closeToolMenu(); menuButton.focus(); }
});
