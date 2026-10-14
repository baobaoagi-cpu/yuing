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
    if (topic !== 'dunni.emotion.v1' || !participant?.identity || !room || !callId ||
        !(payload instanceof Uint8Array) || payload.byteLength > 2048) return null;
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
    constructor({ stage, neutral, clip, blackout, reducedMotion, onStateChange, onFallback,
                  onReceipt, onAck, onAvailabilityChange }) {
      this.stage = stage;
      this.neutral = neutral;
      this.clip = clip;
      this.blackout = blackout;
      this.reducedMotion = reducedMotion;
      this.onStateChange = onStateChange || (() => {});
      this.onFallback = onFallback || (() => {});
      this.onReceipt = onReceipt || (() => {});
      this.onAck = onAck || (() => {});
      this.onAvailabilityChange = onAvailabilityChange || (() => {});
      this.active = false;
      this.neutralReady = false;
      this.phase = 'stopped';
      this.generation = 0;
      this.sequence = 0;
      this.callId = '';
      this.acknowledged = new Set();
      this.currentEvent = null;
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
      this.neutralReady = false;
      this.phase = 'neutral';
      this.stage.hidden = false;
      this.stage.classList.remove('fallback');
      this.clip.hidden = true;
      this.blackout.classList.remove('opaque');
      this.neutral.muted = this.clip.muted = true;
      this.neutral.loop = true;
      this.clip.loop = false;
      this.onStateChange('neutral');
      if (this.reducedMotion.matches) {
        this.stage.classList.add('fallback'); this.onAvailabilityChange(false); return;
      }
      try { this.neutral.currentTime = 0; await this.neutral.play(); this.neutralReady = true; }
      catch (_) { if (this.active) this.fallback(); }
      if (this.active) this.onAvailabilityChange(this.isAvailable());
    }

    setCallId(callId) { this.callId = callId || ''; }

    isAvailable() {
      return this.active && this.enabled && !this.manualOverride &&
        !this.reducedMotion.matches && !this.stage.hidden &&
        !this.stage.classList.contains('fallback') && this.neutralReady &&
        this.phase !== 'stopped';
    }

    acknowledge(event, status) {
      if (!event?.call_id || event.call_id !== this.callId ||
          !Number.isSafeInteger(event.sequence) || this.acknowledged.has(event.sequence)) return;
      this.acknowledged.add(event.sequence);
      this.onAck({call_id:event.call_id,sequence:event.sequence,status});
    }

    cancelOutstanding(status='skipped') {
      this.acknowledge(this.currentEvent,status);
      this.acknowledge(this.pending?.event,status);
      this.currentEvent = null;
      this.pending = null;
    }

    stop() {
      this.cancelOutstanding();
      this.generation++;
      this.active = false;
      this.neutralReady = false;
      this.phase = 'stopped';
      this.sequence = 0;
      this.callId = '';
      this.acknowledged.clear();
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
      this.onAvailabilityChange(false);
    }

    // The caller verifies topic, publisher, room and timestamp before this method.
    acceptEvent(event) {
      if (!this.active || !event || !Number.isSafeInteger(event.sequence) ||
          event.sequence <= this.sequence ||
          (event.state !== 'neutral' && !Object.hasOwn(CLIPS, event.state))) return false;
      this.sequence = event.sequence;
      this.onReceipt({type:'requested',state:event.state,at:Date.now()});
      if (!this.isAvailable() || (event.issued_at && Date.now() - event.issued_at > event.ttl_ms)) {
        this.acknowledge(event,'skipped'); return false;
      }
      if (event.state === 'neutral') {
        this.cancelOutstanding();
        this.currentEvent = event;
        clearTimeout(this.queueTimer);
        this.queueTimer = 0;
        if (this.phase === 'loading') {
          this.generation++;
          this.clip.pause();
          this.clip.hidden = true;
          this.blackout.classList.remove('opaque');
          this.neutral.play().then(() => this.confirmNeutral(event))
            .catch(() => { this.acknowledge(event,'failed'); this.fallback(); });
          this.phase = 'neutral';
          this.onStateChange('neutral');
        } else if (this.phase === 'playing') void this.restore(event);
        else void this.confirmNeutral(event);
        return true;
      }
      if (this.phase === 'loading') {
        this.cancelOutstanding();
        this.generation++;
        this.clip.pause(); this.clip.hidden = true;
        this.blackout.classList.remove('opaque');
        this.phase = 'neutral';
      }
      if (this.phase !== 'neutral') {
        this.acknowledge(this.currentEvent,'skipped');
        this.currentEvent = null;
        this.acknowledge(this.pending?.event,'skipped');
        this.pending = {state:event.state,event};
        return true;
      }
      if (Date.now() - this.lastSwitchAt < 2500) {
        this.acknowledge(this.pending?.event,'skipped');
        this.pending = {state:event.state,event};
        this.schedulePending(2500 - (Date.now() - this.lastSwitchAt));
        return true;
      }
      void this.playEmotion(event.state,event);
      return true;
    }

    setEnabled(enabled) {
      this.enabled = !!enabled;
      if (!this.enabled) {
        this.cancelOutstanding();
        if (this.active) this.onVisibilityChange(true);
      } else if (this.active && !this.reducedMotion.matches) this.onVisibilityChange(false);
      this.manualOverride = false;
      this.onAvailabilityChange(this.isAvailable());
    }

    manualSelect(state) {
      if (!this.active || (state !== 'neutral' && !Object.hasOwn(CLIPS, state))) return false;
      this.cancelOutstanding();
      this.manualOverride = true;
      this.onAvailabilityChange(false);
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
          this.pending = {state,event:null};
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
        if (next.event && next.event.issued_at &&
            Date.now() - next.event.issued_at > next.event.ttl_ms) {
          this.acknowledge(next.event,'skipped'); return;
        }
        void this.playEmotion(next.state,next.event);
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

    async waitVisibleFrame(video, token) {
      if (typeof video.requestVideoFrameCallback === 'function') {
        await new Promise((resolve,reject) => {
          const timeout = setTimeout(() => reject(new Error('first frame timeout')), 1500);
          video.requestVideoFrameCallback(() => { clearTimeout(timeout); resolve(); });
        });
      } else {
        // Safari versions without RVFC: require decoded data and actual playback progress.
        if (video.readyState < 2) throw new Error('no decoded video frame');
        const start = video.currentTime;
        await new Promise((resolve,reject) => {
          const done = error => {
            clearTimeout(timeout);
            video.removeEventListener('timeupdate', updated);
            error ? reject(error) : resolve();
          };
          const updated = () => {
            if (video.currentTime > start + .025 && !video.paused) done();
          };
          const timeout = setTimeout(() => done(new Error('video did not advance')), 1500);
          video.addEventListener('timeupdate',updated);
          updated();
        });
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      if (token !== this.generation || !this.active || this.stage.hidden ||
          this.stage.classList.contains('fallback') || video.hidden)
        throw new Error('frame no longer visible');
    }

    async confirmNeutral(event) {
      const token = this.generation;
      try {
        if (this.reducedMotion.matches || !this.isAvailable()) {
          this.acknowledge(event,'skipped');
          if (this.currentEvent === event) this.currentEvent = null;
          return;
        }
        await this.waitVisibleFrame(this.neutral,token);
        if (this.blackout.classList.contains('opaque')) {
          this.acknowledge(event,'skipped'); return;
        }
        this.acknowledge(event,'presented');
        if (this.currentEvent === event) this.currentEvent = null;
      } catch (_) {
        this.acknowledge(event,'failed');
        if (this.currentEvent === event) this.currentEvent = null;
      }
    }

    async playEmotion(state,event=null) {
      if (!this.active || this.phase !== 'neutral' || !Object.hasOwn(CLIPS, state)) return;
      if (event && event.issued_at && Date.now() - event.issued_at > event.ttl_ms) {
        this.acknowledge(event,'skipped'); return;
      }
      const token = ++this.generation;
      this.currentEvent = event;
      this.phase = 'loading';
      this.clip.hidden = true;
      this.clip.src = CLIPS[state]; // One clip at a time; seven assets are never bulk-preloaded.
      this.clip.preload = 'auto';
      this.clip.load();
      try {
        await this.waitForClip(token);
        if (event && event.issued_at && Date.now() - event.issued_at > event.ttl_ms) {
          this.acknowledge(event,'skipped');
          this.currentEvent = null;
          this.clip.pause(); this.clip.hidden = true;
          this.phase = 'neutral';
          return;
        }
        this.onReceipt({type:'loaded',state,at:Date.now()});
        if (!(await this.fade(true, token))) return;
        if (event && event.issued_at && Date.now() - event.issued_at > event.ttl_ms) {
          this.acknowledge(event,'skipped');
          this.currentEvent = null;
          this.blackout.classList.remove('opaque');
          this.clip.pause(); this.clip.hidden = true;
          this.phase = 'neutral';
          return;
        }
        this.neutralTime = this.neutral.currentTime;
        this.neutral.pause();
        this.clip.currentTime = 0;
        this.clip.hidden = false;
        await this.clip.play();
        if (event) {
          await this.waitVisibleFrame(this.clip,token);
          this.onReceipt({type:'first_frame_visible',state,at:Date.now()});
        }
        if (token !== this.generation || !this.active) return;
        if (event && event.issued_at && Date.now() - event.issued_at > event.ttl_ms) {
          this.acknowledge(event,'skipped');
          this.currentEvent = null;
          this.clip.pause(); this.clip.hidden = true;
          await this.neutral.play();
          if (!(await this.fade(false,token))) return;
          this.phase = 'neutral';
          return;
        }
        this.phase = 'playing';
        if (!(await this.fade(false, token))) return;
        if (token === this.generation && this.active) {
          this.lastSwitchAt = Date.now();
          this.onStateChange(state);
          this.onReceipt({type:'switch_completed',state,at:Date.now()});
          this.acknowledge(event,'presented');
          this.currentEvent = null;
        }
      } catch (error) {
        if (token === this.generation && this.active) {
          this.acknowledge(event,error.message==='video frame callback unavailable'?'skipped':'failed');
          this.currentEvent = null;
          this.fallback();
        }
      }
    }

    async restore(event=null) {
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
        if (event) void this.confirmNeutral(event);
        const next = this.pending;
        if (next) this.schedulePending(this.manualOverride ? 360 :
          2500 - (Date.now() - this.lastSwitchAt));
      } catch (_) {
        this.acknowledge(event,'failed');
        if (token === this.generation) this.fallback();
      }
    }

    fallback() {
      this.cancelOutstanding('failed');
      this.generation++;
      this.neutralReady = false;
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
      this.onAvailabilityChange(false);
    }

    onVisibilityChange(hidden) {
      if (!this.active) return;
      if (hidden || this.reducedMotion.matches) {
        this.cancelOutstanding('skipped');
        this.generation++;
        this.neutralReady = false;
        this.clip.pause();
        this.neutral.pause();
        this.clip.hidden = true;
        this.stage.classList.add('fallback');
        this.blackout.classList.remove('opaque');
        this.phase = 'fallback';
        this.pending = null;
        clearTimeout(this.queueTimer);
        this.onStateChange('neutral');
        this.onAvailabilityChange(false);
      } else {
        this.stage.classList.remove('fallback');
        this.phase = 'neutral';
        this.neutral.play().then(()=>{
          this.neutralReady = true;
          this.onAvailabilityChange(this.isAvailable());
        })
          .catch(() => this.fallback());
      }
    }
  }

  root.YulingEmotionVideo = { EmotionVideoController, CLIPS, decodeAgentEmotionEvent };
  if (typeof module !== 'undefined' && module.exports)
    module.exports = { EmotionVideoController, CLIPS, decodeAgentEmotionEvent };
})(typeof window !== 'undefined' ? window : globalThis);
