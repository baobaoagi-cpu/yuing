// Exercise a touch pull from the top of chat without changing a real account.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'pull-refresh.js'), 'utf8');
function setup() {
  const listeners = new Map();
  const target = { closest: () => null };
  const area = { scrollTop: 0, contains: node => node === target };
  const classes = new Set();
  let calls = 0;
  const document = {
    body: { dataset: { yulingView: 'chat' }, append() {} },
    createElement: () => ({ className: '', classList: {
      add: name => classes.add(name), remove: name => classes.delete(name),
    }, setAttribute() {}, textContent: '' }),
    getElementById: id => id === 'conversation' ? area : null,
    addEventListener: (name, handler) => listeners.set(name, handler),
  };
  const window = {
    getSelection: () => ({ toString: () => '' }),
    YulingRefreshLatest: async () => { calls++; return true; },
  };
  vm.runInNewContext(source, { document, window });
  const event = (type, y) => ({ target, touches: type === 'touchend' ? [] : [{ clientX: 100, clientY: y }],
    changedTouches: [{ clientX: 100, clientY: y }], cancelable: true, preventDefault() {} });
  return { listeners, area, event, calls: () => calls };
}

(async () => {
  const page = setup();
  const send = (type, y) => page.listeners.get(type)(page.event(type, y));
  send('touchstart', 100); send('touchmove', 145); await send('touchend', 145);
  assert.equal(page.calls(), 0, 'short pull does not reload');
  send('touchstart', 100); send('touchmove', 190); await send('touchend', 190);
  assert.equal(page.calls(), 1, 'deliberate pull reloads');
  page.area.scrollTop = 80;
  send('touchstart', 100); send('touchmove', 190); await send('touchend', 190);
  assert.equal(page.calls(), 1, 'scrolling through chat does not reload');
  console.log('Pull to refresh: threshold and scroll guard PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
