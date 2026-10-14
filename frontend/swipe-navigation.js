/* Horizontal navigation shared by chat and the original call page. */
(function () {
  const view = document.body.dataset.yulingView;
  if (view !== 'chat' && view !== 'call') return;
  let touchStart = null;
  let pointerStart = null;
  let wheelDistance = 0;
  let wheelAt = 0;
  let lastNavigationAt = 0;

  function blocked(target) {
    return !!target?.closest?.('button, a, input, textarea, select, canvas, [contenteditable], [data-noswipe], .message, .bubble');
  }
  function selectionActive() {
    return !!window.getSelection?.()?.toString().trim();
  }
  function embeddedInCall() {
    try { return window.parent !== window && window.parent.document.body.dataset.yulingView === 'call'; }
    catch (_) { return false; }
  }
  function canStart(target, x) {
    if (blocked(target) || selectionActive()) return false;
    const region = view === 'chat' ? '#conversation' : '#callTop, #callStage, #callControls';
    if (!target?.closest?.(region) && !(view === 'call' && target === document.body)) return false;
    if (view === 'call' && document.querySelector('.panel:not(.hidden), [role="dialog"]:not(.hidden)')) return false;
    if (view === 'chat' && (document.activeElement?.matches?.('input, textarea, [contenteditable]') ||
        (window.visualViewport && window.visualViewport.height < window.innerHeight - 120))) return false;
    // Keep iPhone's native back/forward edge gesture available.
    return x >= 28 && x <= window.innerWidth - 28;
  }
  function navigate() {
    lastNavigationAt = performance.now();
    if (view === 'call') {
      window.YulingCallNavigation?.showChat();
    } else if (embeddedInCall()) {
      window.parent.postMessage({ type: 'yuling:view-call' }, window.location.origin);
    } else {
      window.location.assign('/call');
    }
  }
  function completed(start, x, y) {
    if (!start || selectionActive() || performance.now() - start.time >= 900) return;
    const dx = x - start.x;
    const dy = y - start.y;
    if ((view === 'chat' ? dx < -72 : dx > 72) && Math.abs(dx) > Math.abs(dy) * 1.5) navigate();
  }

  document.addEventListener('touchstart', event => {
    touchStart = null;
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    if (canStart(event.target, touch.clientX))
      touchStart = { x: touch.clientX, y: touch.clientY, time: performance.now() };
  }, { passive: true });
  document.addEventListener('touchend', event => {
    const start = touchStart;
    touchStart = null;
    if (event.changedTouches.length === 1) {
      const touch = event.changedTouches[0];
      completed(start, touch.clientX, touch.clientY);
    }
  }, { passive: true });
  document.addEventListener('touchcancel', () => { touchStart = null; }, { passive: true });

  document.addEventListener('pointerdown', event => {
    pointerStart = null;
    if (event.pointerType !== 'touch' && event.button === 0 && canStart(event.target, event.clientX))
      pointerStart = { x: event.clientX, y: event.clientY, time: performance.now() };
  });
  document.addEventListener('pointerup', event => {
    const start = pointerStart;
    pointerStart = null;
    if (event.pointerType !== 'touch') completed(start, event.clientX, event.clientY);
  });
  document.addEventListener('pointercancel', () => { pointerStart = null; });

  document.addEventListener('wheel', event => {
    if (event.ctrlKey || !canStart(event.target, event.clientX) ||
        Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.5 ||
        performance.now() - lastNavigationAt < 800) return;
    const now = performance.now();
    if (now - wheelAt > 500 || Math.sign(event.deltaX) !== Math.sign(wheelDistance)) wheelDistance = 0;
    wheelAt = now;
    wheelDistance += event.deltaX;
    if (view === 'chat' ? wheelDistance > 70 : wheelDistance < -70) {
      wheelDistance = 0;
      navigate();
    }
  }, { passive: true });

  if (view === 'chat' && embeddedInCall()) {
    document.querySelector('a.call-link[href="/call"]')?.addEventListener('click', event => {
      event.preventDefault();
      navigate();
    });
  }
})();
