/* Finger-following navigation shared by chat and the original call page. */
(function () {
  const view = document.body.dataset.yulingView;
  if (view !== 'chat' && view !== 'call') return;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const duration = 220;
  let gesture = null;
  let wheelDistance = 0;
  let wheelAt = 0;
  let lastNavigationAt = 0;
  let preview = null;
  let settleTimer = 0;
  const draftKey = 'yuling-swipe-chat-draft';

  function saveChatDraft() {
    if (view !== 'chat' || embeddedInCall()) return;
    const input = document.getElementById('message-input');
    const conversation = document.getElementById('conversation');
    try {
      sessionStorage.setItem(draftKey, JSON.stringify({ text: input?.value || '', scroll: conversation?.scrollTop || 0 }));
    } catch (_) { /* Private browsing may refuse storage. */ }
  }
  function restoreChatDraft() {
    const frame = document.getElementById('chatOverlay');
    try {
      const saved = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
      if (!saved || !frame?.contentDocument) return;
      const input = frame.contentDocument.getElementById('message-input');
      const conversation = frame.contentDocument.getElementById('conversation');
      if (input && !input.value && typeof saved.text === 'string') {
        input.value = saved.text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      if (conversation && Number.isFinite(saved.scroll)) {
        requestAnimationFrame(() => { conversation.scrollTop = saved.scroll; });
      }
      sessionStorage.removeItem(draftKey);
    } catch (_) { /* The same room remains usable if restoration fails. */ }
  }

  function width() { return document.documentElement.clientWidth || window.innerWidth; }
  function afterSettle(callback) {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(callback, reducedMotion.matches ? 0 : duration + 35);
  }
  function callPosition(chatX, sceneX, moving) {
    const overlay = document.getElementById('chatOverlay');
    document.body.classList.toggle('swipe-moving', moving);
    document.body.style.setProperty('--swipe-chat-x', `${chatX}px`);
    document.body.style.setProperty('--swipe-bar-x', `${chatX}px`);
    document.body.style.setProperty('--swipe-scene-x', `${sceneX}px`);
    overlay.style.pointerEvents = moving ? 'none' : '';
  }
  function embeddedPosition(chatX, sceneX, moving) {
    const overlay = document.getElementById('chatOverlay');
    const childBody = overlay.contentDocument?.body;
    document.body.classList.toggle('swipe-moving', moving);
    document.body.style.setProperty('--swipe-chat-x', '0px');
    document.body.style.setProperty('--swipe-bar-x', `${chatX}px`);
    document.body.style.setProperty('--swipe-scene-x', `${sceneX}px`);
    overlay.style.pointerEvents = '';
    childBody?.classList.toggle('swipe-moving', moving);
    childBody?.style.setProperty('--swipe-chat-x', `${chatX}px`);
  }
  function syncCallView() {
    if (view !== 'call') return;
    clearTimeout(settleTimer);
    const visible = !document.getElementById('chatOverlay').hidden;
    callPosition(visible ? 0 : -width(), visible ? width() : 0, false);
    document.getElementById('chatOverlay').contentDocument?.body?.style.setProperty('--swipe-chat-x', '0px');
  }
  if (view === 'call') {
    document.getElementById('chatOverlay')?.addEventListener('load', restoreChatDraft);
    window.YulingSwipeNavigation = {
      sync: syncCallView,
      beginFromChat() {
        if (document.getElementById('chatOverlay').hidden) return false;
        clearTimeout(settleTimer);
        embeddedPosition(0, width(), true);
        return true;
      },
      updateFromChat(dx) {
        const offset = Math.max(-width(), Math.min(0, dx));
        embeddedPosition(offset, width() + offset, true);
      },
      endFromChat(commit) {
        embeddedPosition(commit ? -width() : 0, commit ? 0 : width(), false);
        if (commit) afterSettle(() => window.YulingCallNavigation?.showCall());
      }
    };
    syncCallView();
  }
  function embeddedParent() {
    try { return window.parent.YulingSwipeNavigation; } catch (_) { return null; }
  }
  function ensurePreview() {
    if (preview) return;
    preview = document.createElement('div');
    preview.id = 'swipe-call-preview';
    preview.setAttribute('aria-hidden', 'true');
    preview.innerHTML = '<div class="swipe-call-fallback"><span>陈端倪</span><small>语音通话</small><i aria-hidden="true">☎</i></div>';
    const frame = document.createElement('iframe');
    frame.title = '语音通话预览';
    frame.tabIndex = -1;
    frame.addEventListener('load', () => preview.classList.add('ready'), { once: true });
    frame.src = '/call';
    preview.append(frame);
    document.body.append(preview);
  }
  function beginVisual() {
    if (!gesture || gesture.visual) return;
    clearTimeout(settleTimer);
    gesture.visual = true;
    if (view === 'call') {
      const overlay = document.getElementById('chatOverlay');
      if (overlay.getAttribute('src') === 'about:blank') {
        document.body.classList.add('swipe-chat-loading');
        overlay.addEventListener('load', () => document.body.classList.remove('swipe-chat-loading'), { once: true });
        overlay.src = '/chat';
      }
      overlay.hidden = false;
      overlay.setAttribute('aria-hidden', 'false');
      callPosition(-width(), 0, true);
      window.YulingCallNavigation?.syncInCallBar?.();
    } else if (embeddedInCall()) {
      gesture.visual = !!embeddedParent()?.beginFromChat();
    } else {
      ensurePreview();
      document.body.classList.add('swipe-moving');
      document.body.style.setProperty('--swipe-chat-x', '0px');
      document.body.style.setProperty('--swipe-preview-x', `${width()}px`);
    }
  }
  function updateVisual(dx) {
    if (!gesture?.visual) return;
    if (view === 'call') {
      const offset = Math.max(0, Math.min(width(), dx));
      callPosition(offset - width(), offset, true);
    } else if (embeddedInCall()) {
      embeddedParent()?.updateFromChat(dx);
    } else {
      const offset = Math.max(-width(), Math.min(0, dx));
      document.body.style.setProperty('--swipe-chat-x', `${offset}px`);
      document.body.style.setProperty('--swipe-preview-x', `${width() + offset}px`);
    }
  }
  function endVisual(commit) {
    if (!gesture?.visual) return;
    if (view === 'call') {
      callPosition(commit ? 0 : -width(), commit ? width() : 0, false);
      if (commit) afterSettle(() => window.YulingCallNavigation?.showChat());
      else afterSettle(() => {
        const overlay = document.getElementById('chatOverlay');
        overlay.hidden = true;
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('swipe-chat-loading');
        window.YulingCallNavigation?.syncInCallBar?.();
      });
    } else if (embeddedInCall()) {
      embeddedParent()?.endFromChat(commit);
    } else {
      document.body.classList.remove('swipe-moving');
      document.body.style.setProperty('--swipe-chat-x', commit ? `${-width()}px` : '0px');
      document.body.style.setProperty('--swipe-preview-x', commit ? '0px' : `${width()}px`);
      if (commit) afterSettle(() => { saveChatDraft(); window.location.assign('/call'); });
    }
  }

  function blocked(target) {
    return !!target?.closest?.('button, a, input, textarea, select, canvas, [contenteditable], [data-noswipe]');
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
    const region = view === 'chat' ? '#conversation' : '.call-toolbar, #callTop, #callStage, #callControls';
    if (!target?.closest?.(region) && !(view === 'call' && target === document.body)) return false;
    if (view === 'call' && document.querySelector('.panel:not(.hidden), [role="dialog"]:not(.hidden)')) return false;
    if (view === 'chat' && (document.activeElement?.matches?.('input, textarea, [contenteditable]') ||
        (window.visualViewport && window.visualViewport.height < window.innerHeight - 120))) return false;
    // Safari owns the iPhone edge gesture. Android's system edge swipe may
    // preempt this handler, but a gesture delivered to the page should work.
    const edge = /iP(hone|od|ad)/.test(navigator.userAgent) ? 28 : 0;
    return x >= edge && x <= window.innerWidth - edge;
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
  function start(target, x, y) {
    gesture = canStart(target, x) ? { x, y, lastX: x, locked: false, vertical: false, visual: false } : null;
  }
  function move(x, y, event) {
    if (!gesture || gesture.vertical) return;
    const dx = x - gesture.x, dy = y - gesture.y;
    gesture.lastX = x;
    if (!gesture.locked) {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      if (Math.abs(dy) >= Math.abs(dx) || (view === 'chat' ? dx >= 0 : dx <= 0)) {
        gesture.vertical = true;
        return;
      }
      gesture.locked = true;
      beginVisual();
    }
    if (gesture.visual) {
      if (event?.cancelable) event.preventDefault();
      updateVisual(dx);
    }
  }
  function end(x, cancelled) {
    if (!gesture) return;
    const current = gesture;
    const dx = x - current.x;
    const commit = !cancelled && current.visual && Math.abs(dx) >= Math.min(110, width() * .28);
    endVisual(commit);
    if (commit) lastNavigationAt = performance.now();
    gesture = null;
  }

  document.addEventListener('touchstart', event => {
    if (event.touches.length !== 1) { gesture = null; return; }
    const touch = event.touches[0];
    start(event.target, touch.clientX, touch.clientY);
  }, { passive: true });
  document.addEventListener('touchmove', event => {
    if (event.touches.length !== 1) { end(gesture?.lastX || 0, true); return; }
    const touch = event.touches[0];
    move(touch.clientX, touch.clientY, event);
  }, { passive: false });
  document.addEventListener('touchend', event => {
    if (event.changedTouches.length === 1) {
      const touch = event.changedTouches[0];
      end(touch.clientX, false);
    }
  }, { passive: true });
  document.addEventListener('touchcancel', () => end(gesture?.lastX || 0, true), { passive: true });

  document.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'touch' && event.button === 0 && canStart(event.target, event.clientX))
      start(event.target, event.clientX, event.clientY);
  });
  document.addEventListener('pointermove', event => {
    if (event.pointerType !== 'touch' && event.buttons === 1) move(event.clientX, event.clientY, event);
  });
  document.addEventListener('pointerup', event => {
    if (event.pointerType !== 'touch') end(event.clientX, false);
  });
  document.addEventListener('pointercancel', event => {
    if (event.pointerType !== 'touch') end(gesture?.lastX || 0, true);
  });
  window.addEventListener('resize', () => {
    if (gesture?.visual) return;
    if (view === 'call') syncCallView();
    else if (preview) document.body.style.setProperty('--swipe-preview-x', `${width()}px`);
  });

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
    document.documentElement.classList.add('embedded-in-call');
    document.body.classList.add('embedded-in-call');
    document.querySelector('a.call-link[href="/call"]')?.addEventListener('click', event => {
      event.preventDefault();
      navigate();
    });
  }
})();
