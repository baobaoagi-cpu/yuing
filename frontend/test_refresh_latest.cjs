// A new chat iframe must be able to upgrade an older, idle call shell.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'update.js'), 'utf8');
function setup({ embedded = false, callState = '拨打', draft = '' } = {}) {
  const replaced = [];
  const label = { textContent: '', title: '' };
  const button = {
    disabled: false, classList: { add() {}, remove() {} },
    setAttribute() {}, addEventListener() {},
  };
  const oldCall = {
    document: {
      body: { dataset: { yulingView: 'call' } },
      getElementById(id) {
        if (id === 'callLabel') return { textContent: callState };
        if (id === 'callCircle') return { classList: { contains: name => name === 'go' && callState === '拨打' } };
        return null;
      },
    },
    location: { replace: url => replaced.push({ target: 'parent', url }) },
  };
  const window = {
    location: { origin: 'https://example.test', replace: url => replaced.push({ target: 'self', url }) },
    confirm: () => true,
  };
  window.parent = embedded ? oldCall : window;
  const document = {
    body: { dataset: { yulingView: 'chat' } },
    getElementById(id) {
      return ({ 'refresh-button': button, 'deployment-version': label,
        'message-input': { value: draft }, attachment: { hidden: true },
        'url-preview': { hidden: true }, 'page-reference': { hidden: true } })[id] || null;
    },
    querySelector: () => null,
  };
  vm.runInNewContext(source, {
    window, document, fetch: async () => ({ ok: true, json: async () => ({ version: 'v2', cache_key: 'v2' }) }),
    URL, Date,
  });
  return { window, replaced, label };
}

(async () => {
  const idle = setup({ embedded: true });
  assert.equal(await idle.window.YulingRefreshLatest(), true);
  assert.equal(idle.replaced.length, 1);
  assert.equal(idle.replaced[0].target, 'parent');
  assert.match(idle.replaced[0].url, /\/call\?update=v2&view=chat/);

  const active = setup({ embedded: true, callState: '挂断' });
  assert.equal(await active.window.YulingRefreshLatest(), false);
  assert.equal(active.replaced.length, 0, 'active calls cannot be interrupted');

  const standalone = setup();
  assert.equal(await standalone.window.YulingRefreshLatest(), true);
  assert.match(standalone.replaced[0].url, /\/chat\?update=v2/);
  console.log('Refresh: old embedded call shell, active-call guard, standalone chat PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
