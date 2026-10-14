/* Ephemeral in-call drawings. No markup from the Agent is inserted into the DOM. */
(function (root) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const KINDS = new Set(['sun', 'heart', 'text', 'strokes']);
  const BRUSHES = new Set(['solid', 'glow', 'soft']);
  const numberIn = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

  function decodeAgentSketchEvent({payload, participant, topic, room, callId, isAgent, now = Date.now()}) {
    if (topic !== 'dunni.sketch.v1' || !participant?.identity || !room || !callId ||
        !(payload instanceof Uint8Array) || payload.byteLength > 8192) return null;
    const sender = Array.from(room.remoteParticipants.values()).find(p =>
      p.identity === participant.identity && (!p.sid || !participant.sid || p.sid === participant.sid));
    if (!sender || !isAgent(sender)) return null;
    let event;
    try { event = JSON.parse(new TextDecoder().decode(payload)); } catch (_) { return null; }
    if (!event || event.call_id !== callId ||
        !Number.isSafeInteger(event.sequence) || event.sequence < 1 ||
        !Number.isSafeInteger(event.issued_at) || !Number.isSafeInteger(event.ttl_ms) ||
        !numberIn(event.ttl_ms, 1, 10000) || event.issued_at > now + 1000 ||
        now - event.issued_at > event.ttl_ms || !KINDS.has(event.kind) ||
        !event.anchor || !numberIn(event.anchor.x, 0, 1) || !numberIn(event.anchor.y, 0, 1) ||
        !numberIn(event.size, .05, .35) || !/^#[0-9a-fA-F]{6}$/.test(event.color || '') ||
        !Number.isSafeInteger(event.duration_ms) || !numberIn(event.duration_ms, 500, 12000) ||
        (event.brush !== undefined && !BRUSHES.has(event.brush))) return null;
    if (event.kind === 'text' && (typeof event.text !== 'string' ||
        !event.text.trim() || event.text.length > 16 || /[\u0000-\u001f\u007f]/.test(event.text))) return null;
    if (event.kind === 'strokes') {
      if (!Array.isArray(event.strokes) || !event.strokes.length || event.strokes.length > 12) return null;
      let points = 0;
      for (const stroke of event.strokes) {
        if (!Array.isArray(stroke) || stroke.length < 2 || stroke.length > 64) return null;
        points += stroke.length;
        if (points > 256 || !stroke.every(p => Array.isArray(p) && p.length === 2 &&
            numberIn(p[0], 0, 1) && numberIn(p[1], 0, 1))) return null;
      }
    }
    return {...event, brush:event.brush || 'solid'};
  }

  function element(name, attrs = {}) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }

  function shape(event) {
    const svg = element('svg', {viewBox:'0 0 100 100', 'aria-hidden':'true'});
    svg.style.cssText = 'display:block;width:100%;height:100%;overflow:visible';
    if (event.brush === 'glow') svg.style.filter = `drop-shadow(0 0 6px ${event.color})`;
    if (event.brush === 'soft') svg.style.opacity = '.72';
    const base = {fill:'none', stroke:event.color, 'stroke-width':event.brush === 'soft' ? 6 : 4,
      'stroke-linecap':'round', 'stroke-linejoin':'round'};
    const paths = [];
    if (event.kind === 'sun') {
      paths.push('M72 50c0 12-10 22-22 22S28 62 28 50 38 28 50 28s22 10 22 22',
        'M50 7v12 M50 81v12 M7 50h12 M81 50h12 M19 19l9 9 M72 72l9 9 M81 19l-9 9 M28 72l-9 9');
    } else if (event.kind === 'heart') {
      paths.push('M50 85C37 73 13 58 13 39c0-13 16-23 29-13l8 8 8-8c13-10 29 0 29 13 0 19-24 34-37 46Z');
    } else if (event.kind === 'strokes') {
      for (const stroke of event.strokes) paths.push(stroke.map((p,i) =>
        `${i ? 'L' : 'M'}${(p[0]*100).toFixed(2)} ${(p[1]*100).toFixed(2)}`).join(' '));
    }
    for (const d of paths) svg.appendChild(element('path', {...base, d}));
    if (event.kind === 'text') {
      const text = element('text', {x:50, y:62, fill:event.color,
        'text-anchor':'middle', 'font-size':event.text.length > 8 ? 15 : 26,
        'font-family':'ui-rounded, system-ui, sans-serif', 'font-weight':600});
      text.textContent = event.text;
      svg.appendChild(text);
    }
    return svg;
  }

  class LiveSketchController {
    constructor({layer, reducedMotion, onAck, getAvoidRects, canDisplay, raf, now}) {
      this.layer = layer;
      this.reducedMotion = reducedMotion;
      this.onAck = onAck || (() => {});
      this.getAvoidRects = getAvoidRects || (() => []);
      this.canDisplay = canDisplay || (() => true);
      this.raf = raf || (callback => requestAnimationFrame(callback));
      this.now = now || (() => Date.now());
      this.active = false;
      this.enabled = true;
      this.callId = '';
      this.sequence = 0;
      this.generation = 0;
      this.drawings = [];
    }

    start(callId) {
      this.stop();
      this.active = !!callId;
      this.callId = callId || '';
    }

    stop() {
      this.active = false;
      this.callId = '';
      this.sequence = 0;
      this.clear();
    }

    clear() {
      this.generation++;
      for (const drawing of this.drawings) clearTimeout(drawing.timer);
      this.drawings = [];
      this.layer.replaceChildren();
    }

    setEnabled(value) {
      this.enabled = !!value;
      if (!this.enabled) this.clear();
    }

    place(event) {
      const width = this.layer.clientWidth || innerWidth;
      const height = this.layer.clientHeight || innerHeight;
      const size = Math.min(width, height) * event.size;
      const preferred = {x:event.anchor.x * width, y:event.anchor.y * height};
      const avoid = this.getAvoidRects();
      const x = Math.max(8, Math.min(width-size-8, preferred.x-size/2));
      const y = Math.max(8, Math.min(height-size-8, preferred.y-size/2));
      const box = {left:x-10,top:y-10,right:x+size+10,bottom:y+size+10};
      // Unsafe requested position is skipped; never silently move it elsewhere.
      if (!avoid.every(r => box.right < r.left || box.left > r.right ||
            box.bottom < r.top || box.top > r.bottom)) return null;
      return {x,y,size};
    }

    async acceptEvent(event) {
      if (!event || !this.active || event.call_id !== this.callId) return false;
      if (event.sequence <= this.sequence) return false;
      this.sequence = event.sequence;
      const ack = status => this.onAck({call_id:event.call_id,sequence:event.sequence,status});
      if (!this.enabled || this.now() - event.issued_at > event.ttl_ms ||
          document.hidden || !this.layer.isConnected || !this.canDisplay()) {
        ack('skipped'); return false;
      }
      const location = this.place(event);
      if (!location) { ack('skipped'); return false; }
      const generation = this.generation;
      try {
        const holder = document.createElement('div');
        holder.className = 'live-sketch-item';
        holder.style.cssText = `position:absolute;left:${location.x}px;top:${location.y}px;`+
          `width:${location.size}px;height:${location.size}px;pointer-events:none`;
        const svg = shape(event);
        const marks = Array.from(svg.querySelectorAll('path'));
        const text = svg.querySelector('text');
        if (!this.reducedMotion.matches) {
          marks.forEach((path,index) => {
            if (typeof path.animate !== 'function' || typeof path.getTotalLength !== 'function') return;
            const length = path.getTotalLength();
            if (!length) return;
            path.style.strokeDasharray = String(length);
            path.style.strokeDashoffset = String(index ? length : length*.94);
          });
          if (text?.animate) text.style.opacity = '.25';
        }
        holder.appendChild(svg);
        this.layer.appendChild(holder);
        const drawing = {event,holder,timer:0};
        this.drawings.push(drawing);
        while (this.drawings.length > 3) {
          const old = this.drawings.shift();
          clearTimeout(old.timer);
          old.holder.remove();
        }
        // ACK only after the browser has had a paint opportunity.
        await new Promise(resolve => this.raf(() => this.raf(resolve)));
        if (generation !== this.generation || !this.active || !holder.isConnected ||
            document.hidden || !this.canDisplay()) {
          holder.remove();
          this.drawings = this.drawings.filter(item => item !== drawing);
          ack('skipped'); return false;
        }
        ack('rendered');
        if (!this.reducedMotion.matches) {
          marks.forEach((path,index) => {
            if (typeof path.animate !== 'function' || typeof path.getTotalLength !== 'function') return;
            const length = path.getTotalLength();
            if (!length) return;
            path.style.strokeDasharray = String(length);
            const start = index ? length : length*.94;
            path.animate([{strokeDashoffset:start},{strokeDashoffset:0}],
              {duration:Math.min(1000,500+marks.length*90),delay:index*130,
                fill:'both',easing:'ease-out'});
          });
          text?.animate?.([{opacity:.25},{opacity:1}],{duration:450,fill:'both'});
        }
        drawing.timer = setTimeout(() => {
          holder.remove();
          this.drawings = this.drawings.filter(item => item !== drawing);
        }, event.duration_ms);
        return true;
      } catch (_) { ack('failed'); return false; }
    }

    redraw() {
      if (!this.active) return;
      for (const drawing of this.drawings) {
        const p = this.place(drawing.event);
        if (!p) { drawing.holder.hidden = true; continue; }
        drawing.holder.hidden = false;
        Object.assign(drawing.holder.style,{left:`${p.x}px`,top:`${p.y}px`,
          width:`${p.size}px`,height:`${p.size}px`});
      }
    }
  }

  const api = {decodeAgentSketchEvent, LiveSketchController};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.YulingLiveSketch = api;
})(typeof window !== 'undefined' ? window : globalThis);
