/* The video layer is visual only. LiveKit audio remains the sole sound source. */
(function (root) {
  'use strict';

  const CLIPS = Object.freeze({
    happy: '/yuling-media/dunni-emotion-happy-v1.mp4',
    curious: '/yuling-media/dunni-emotion-curious-v1.mp4',
    unhappy: '/yuling-media/dunni-emotion-unhappy-v1.mp4',
    sad: '/yuling-media/dunni-emotion-sad-v1.mp4',
    angry: '/yuling-media/dunni-emotion-angry-v1.mp4',
    delighted: '/yuling-media/dunni-emotion-delighted-v1.mp4',
    playful_face: '/yuling-media/dunni-emotion-playful_face-v1.mp4'
  });

  function decodeAgentEmotionEvent({ payload, participant, topic, room, callId, isAgent, now = Date.now() }) {
    if (topic !== 'dunni.emotion.v1' || !participant?.identity || !room || !callId) return null;
    const sender = Array.from(room.remoteParticipants.values()).find(p =>
      p.identity === participant.identity && (!p.sid || !participant.sid || p.sid === participant.sid));
    if (!sender || !isAgent(sender)) return null;
    let event;
    try { event = JSON.parse(new TextDecoder().decode(payload)); } catch (_) { return null; }
    if (event.call_id !== callId || !Number.isSafeInteger(event.sequence) || event.sequence < 1 ||
        !Number.isSafeInteger(event.issued_at) || !Number.isSafeInteger(event.ttl_ms) ||
        event.ttl_ms < 1 || event.ttl_ms > 10000 || event.issued_at > now + 1000 ||
        now - event.issued_at > event.ttl_ms ||
        (event.state !== 'neutral' && !Object.hasOwn(CLIPS, event.state))) return null;
    return event;
  }

  class EmotionVideoController {
    constructor({ stage, neutral, clip, blackout, reducedMotion, onStateChange, onFallback, onReceipt }) {
      this.stage = stage;
      this.neutral = neutral;
      this.clip = clip;
      this.blackout = blackout;
      this.reducedMotion = reducedMotion;
      this.onStateChange = onStateChange || (() => {});
      this.onFallback = onFallback || (() => {});
      this.onReceipt = onReceipt || (() => {});
      this.active = false;
      this.phase = 'stopped';
      this.generation = 0;
      this.sequence = 0;
      this.pending = null;
      this.lastSwitchAt = 0;
      this.neutralTime = 0;
      this.queueTimer = 0;
      this.enabled = true;
      this.manualOverride = false;
      this.neutral.onerror = () => { if (this.active) this.fallback(); };
      this.clip.onended = () => { void this.restore(); };
      this.clip.onerror = () => { if (this.active && this.phase === 'playing') this.fallback(); };
    }

    async start() {
      this.stop();
      this.active = true;
      this.phase = 'neutral';
      this.stage.hidden = false;
      this.stage.classList.remove('fallback');
      this.clip.hidden = true;
      this.blackout.classList.remove('opaque');
      this.neutral.muted = this.clip.muted = true;
      this.neutral.loop = true;
      this.clip.loop = false;
      this.onStateChange('neutral');
      if (this.reducedMotion.matches) { this.stage.classList.add('fallback'); return; }
      try { this.neutral.currentTime = 0; await this.neutral.play(); }
      catch (_) { if (this.active) this.fallback(); }
    }

    stop() {
      this.generation++;
      this.active = false;
      this.phase = 'stopped';
      this.sequence = 0;
      this.pending = null;
      this.manualOverride = false;
      clearTimeout(this.queueTimer);
      this.queueTimer = 0;
      this.neutral.pause();
      this.clip.pause();
      this.clip.hidden = true;
      this.clip.removeAttribute('src');
      this.clip.load();
      this.blackout.classList.remove('opaque');
      this.stage.classList.remove('fallback');
      this.stage.hidden = true;
      this.onStateChange('neutral');
    }

    // The caller verifies topic, publisher, room and timestamp before this method.
    acceptEvent(event) {
      if (!this.active || !this.enabled || this.manualOverride || !event ||
          !Number.isSafeInteger(event.sequence) ||
          event.sequence <= this.sequence ||
          (event.state !== 'neutral' && !Object.hasOwn(CLIPS, event.state))) return false;
      this.sequence = event.sequence;
      this.onReceipt({type:'requested',state:event.state,at:Date.now()});
      if (event.state === 'neutral') {
        this.pending = null;
        clearTimeout(this.queueTimer);
        this.queueTimer = 0;
        if (this.phase === 'loading') {
          this.generation++;
          this.clip.pause();
          this.clip.hidden = true;
          this.blackout.classList.remove('opaque');
          this.neutral.play().catch(() => this.fallback());
          this.phase = 'neutral';
          this.onStateChange('neutral');
        } else if (this.phase === 'playing') void this.restore();
        return true;
      }
      if (this.reducedMotion.matches || this.stage.classList.contains('fallback')) return true;
      if (this.phase !== 'neutral') {
        this.pending = event.state;
        return true;
      }
      if (Date.now() - this.lastSwitchAt < 2500) {
        this.pending = event.state;
        this.schedulePending(2500 - (Date.now() - this.lastSwitchAt));
        return true;
      }
      void this.playEmotion(event.state);
      return true;
    }

    setEnabled(enabled) {
      this.enabled = !!enabled;
      if (!this.enabled) this.manualSelect('neutral');
      this.manualOverride = false;
    }

    manualSelect(state) {
      if (!this.active || (state !== 'neutral' && !Object.hasOwn(CLIPS, state))) return false;
      this.manualOverride = true;
      clearTimeout(this.queueTimer);
      this.pending = null;
      if (state === 'neutral') {
        if (this.phase === 'playing') void this.restore();
        else if (this.phase === 'loading') {
          this.generation++;
          this.clip.pause();
          this.clip.hidden = true;
          this.blackout.classList.remove('opaque');
          this.phase = 'neutral';
          this.neutral.play().catch(() => this.fallback());
          this.onStateChange('neutral');
        }
      } else if (!this.reducedMotion.matches && !this.stage.classList.contains('fallback')) {
        if (this.phase === 'playing') {
          this.pending = state;
          void this.restore();
        } else if (this.phase === 'loading') {
          this.manualSelect('neutral');
          void this.playEmotion(state);
        } else if (this.phase === 'neutral') void this.playEmotion(state);
      }
      return true;
    }

    schedulePending(delay) {
      clearTimeout(this.queueTimer);
      this.queueTimer = setTimeout(() => {
        this.queueTimer = 0;
        if (!this.active || this.phase !== 'neutral' || !this.pending) return;
        const next = this.pending;
        this.pending = null;
        void this.playEmotion(next);
      }, Math.max(0, delay));
    }

    async waitForClip(token) {
      if (this.clip.readyState >= 2) return;
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => done(new Error('video load timeout')), 5000);
        const done = error => {
          clearTimeout(timer);
          this.clip.removeEventListener('loadeddata', loaded);
          this.clip.removeEventListener('error', failed);
          error ? reject(error) : resolve();
        };
        const loaded = () => done();
        const failed = () => done(new Error('video decode failed'));
        this.clip.addEventListener('loadeddata', loaded, { once: true });
        this.clip.addEventListener('error', failed, { once: true });
        if (this.clip.readyState >= 2) done();
      });
      if (token !== this.generation) throw new Error('superseded');
    }

    async fade(black, token) {
      this.blackout.classList.toggle('opaque', black);
      await new Promise(resolve => setTimeout(resolve, this.reducedMotion.matches ? 0 : 220));
      return token === this.generation && this.active;
    }

    async playEmotion(state) {
      if (!this.active || this.phase !== 'neutral' || !Object.hasOwn(CLIPS, state)) return;
      const token = ++this.generation;
      this.phase = 'loading';
      this.clip.hidden = true;
      this.clip.src = CLIPS[state]; // One clip at a time; seven assets are never bulk-preloaded.
      this.clip.preload = 'auto';
      this.clip.load();
      try {
        await this.waitForClip(token);
        this.onReceipt({type:'loaded',state,at:Date.now()});
        if (!(await this.fade(true, token))) return;
        this.neutralTime = this.neutral.currentTime;
        this.neutral.pause();
        this.clip.currentTime = 0;
        this.clip.hidden = false;
        await this.clip.play();
        if (typeof this.clip.requestVideoFrameCallback === 'function') {
          await new Promise((resolve,reject) => {
            const timeout = setTimeout(() => reject(new Error('first frame timeout')), 1000);
            this.clip.requestVideoFrameCallback(() => { clearTimeout(timeout); resolve(); });
          });
          this.onReceipt({type:'first_frame_visible',state,at:Date.now()});
        }
        if (token !== this.generation || !this.active) return;
        this.phase = 'playing';
        await this.fade(false, token);
        if (token === this.generation && this.active) {
          this.lastSwitchAt = Date.now();
          this.onStateChange(state);
          this.onReceipt({type:'switch_completed',state,at:Date.now()});
        }
      } catch (_) {
        if (token === this.generation && this.active) this.fallback();
      }
    }

    async restore() {
      if (!this.active || this.phase !== 'playing') return;
      const token = ++this.generation;
      this.phase = 'restoring';
      if (!(await this.fade(true, token))) return;
      this.clip.pause();
      this.clip.hidden = true;
      try {
        this.neutral.currentTime = this.neutralTime;
        await this.neutral.play();
        if (!(await this.fade(false, token))) return;
        this.phase = 'neutral';
        this.onStateChange('neutral');
        const next = this.pending;
        if (next) this.schedulePending(this.manualOverride ? 360 :
          2500 - (Date.now() - this.lastSwitchAt));
      } catch (_) { if (token === this.generation) this.fallback(); }
    }

    fallback() {
      this.generation++;
      this.neutral.pause();
      this.clip.pause();
      this.clip.hidden = true;
      this.blackout.classList.remove('opaque');
      this.stage.classList.add('fallback');
      this.phase = 'fallback';
      this.pending = null;
      clearTimeout(this.queueTimer);
      this.onStateChange('neutral');
      this.onReceipt({type:'fallback',state:'neutral',at:Date.now()});
      this.onFallback();
    }

    onVisibilityChange(hidden) {
      if (!this.active) return;
      if (hidden || this.reducedMotion.matches) {
        this.generation++;
        this.clip.pause();
        this.neutral.pause();
        this.clip.hidden = true;
        this.stage.classList.add('fallback');
        this.blackout.classList.remove('opaque');
        this.phase = 'fallback';
        this.pending = null;
        clearTimeout(this.queueTimer);
        this.onStateChange('neutral');
      } else {
        this.stage.classList.remove('fallback');
        this.phase = 'neutral';
        this.neutral.play().catch(() => this.fallback());
      }
    }
  }

  root.YulingEmotionVideo = { EmotionVideoController, CLIPS, decodeAgentEmotionEvent };
  if (typeof module !== 'undefined' && module.exports)
    module.exports = { EmotionVideoController, CLIPS, decodeAgentEmotionEvent };
})(typeof window !== 'undefined' ? window : globalThis);
