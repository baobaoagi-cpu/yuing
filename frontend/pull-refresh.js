/* Refresh the current voice or text view only after a deliberate pull from its top. */
(() => {
  const view = document.body.dataset.yulingView;
  if (view !== 'chat' && view !== 'call') return;
  const scrollArea = view === 'chat' ? document.getElementById('conversation') : null;
  const indicator = document.createElement('div');
  indicator.className = 'pull-refresh-indicator';
  indicator.setAttribute('role', 'status');
  indicator.setAttribute('aria-live', 'polite');
  indicator.textContent = '下拉取得最新版本';
  document.body.append(indicator);

  const threshold = 76;
  let gesture = null;
  let refreshing = false;

  function reset() {
    gesture = null;
    if (!refreshing) indicator.classList.remove('is-visible');
  }
  function excluded(target) {
    return target?.closest?.('button, a, input, textarea, select, iframe, [contenteditable], [data-noswipe]');
  }
  function canStart(target) {
    if (excluded(target) || window.getSelection?.()?.toString().trim()) return false;
    if (view === 'chat') return scrollArea?.contains(target) && scrollArea.scrollTop <= 1;
    if (document.getElementById('chatOverlay')?.hidden === false) return false;
    return !document.querySelector('.panel:not(.hidden), [role="dialog"]:not(.hidden)');
  }

  document.addEventListener('touchstart', event => {
    if (refreshing || event.touches.length !== 1 || !canStart(event.target)) {
      reset();
      return;
    }
    const touch = event.touches[0];
    gesture = { x: touch.clientX, y: touch.clientY, pulling: false, ready: false };
  }, { passive: true });

  document.addEventListener('touchmove', event => {
    if (!gesture || event.touches.length !== 1) return;
    const touch = event.touches[0];
    const dx = touch.clientX - gesture.x;
    const dy = touch.clientY - gesture.y;
    if (dy < 0 || Math.abs(dx) > Math.abs(dy) + 8 || (scrollArea && scrollArea.scrollTop > 1)) {
      reset();
      return;
    }
    if (dy < 10) return;
    gesture.pulling = true;
    gesture.ready = dy >= threshold;
    indicator.textContent = gesture.ready ? '鬆開更新' : '下拉取得最新版本';
    indicator.classList.add('is-visible');
    if (event.cancelable) event.preventDefault();
  }, { passive: false });

  document.addEventListener('touchend', async event => {
    if (!gesture) return;
    const ready = gesture.pulling && gesture.ready && event.changedTouches.length === 1;
    gesture = null;
    if (!ready) { reset(); return; }
    const refresh = window.YulingRefreshLatest;
    if (typeof refresh !== 'function') { reset(); return; }
    refreshing = true;
    indicator.textContent = '正在取得最新版本';
    try { await refresh(); }
    finally {
      refreshing = false;
      indicator.classList.remove('is-visible');
    }
  }, { passive: true });
  document.addEventListener('touchcancel', reset, { passive: true });
})();
