const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '../website/assets/touch-interaction.js');

function target(properties = {}) {
  const listeners = new Map();
  return Object.assign({
    style: {}, dataset: {},
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
    emit(type, event = {}) {
      for (const callback of listeners.get(type) || []) callback(event);
    },
  }, properties);
}

function fixture({ reducedMotion = false } = {}) {
  let now = 0, frameId = 0;
  const frames = new Map();
  const imageData = (width, height) => ({ data: new Uint8ClampedArray(width * height * 4) });
  // Rendering is a browser concern. Keep the real production script, event
  // handlers, clock, and animation scheduling; stub only the graphics APIs.
  const context2d = () => new Proxy({
    getImageData: (_x, _y, width, height) => imageData(width, height),
    createImageData: imageData,
    createRadialGradient: () => ({ addColorStop() {} }),
  }, { get: (object, key) => key in object ? object[key] : () => {} });
  const gl = new Proxy({
    NO_ERROR: 0,
    getError: () => 0,
    getShaderParameter: () => true,
    getProgramParameter: () => true,
    getAttribLocation: (_program, name) => name === 'position' ? 0 : 1,
  }, {
    get(object, key) {
      if (key in object) return object[key];
      return /^[A-Z_0-9]+$/.test(key) ? 1 : () => {};
    },
  });
  const image = target({ complete: true, naturalWidth: 1774, naturalHeight: 887 });
  const art = target({
    clientWidth: 887, clientHeight: 444,
    querySelector: () => image,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 887, height: 444 }),
  });
  const canvas = target({ getContext: () => gl });
  const stage = target({ querySelector: () => art });
  const status = target();
  const document = target({
    hidden: false,
    getElementById: id => ({
      'ag-touch-stage': stage, 'ag-touch-light': canvas, 'ag-touch-status': status,
    })[id],
    createElement: () => target({ width: 300, height: 150, getContext: context2d }),
  });
  class Observer { observe() {} disconnect() {} }
  vm.runInNewContext(readFileSync(scriptPath, 'utf8'), {
    document, window: target(), devicePixelRatio: 1,
    matchMedia: () => target({ matches: reducedMotion }),
    ResizeObserver: Observer, IntersectionObserver: Observer,
    performance: { now: () => now },
    getComputedStyle: () => ({ maskImage: 'none', webkitMaskImage: 'none' }),
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
  }, { filename: scriptPath });
  const result = {
    stage, canvas, image, art, document,
    states: () => JSON.parse(stage.dataset.pulseStates),
    pendingFrames: () => frames.size,
    advance(milliseconds) {
      assert.ok(milliseconds >= now, 'the fixture clock must be monotonic');
      now = milliseconds;
      const callbacks = [...frames.values()];
      frames.clear();
      for (const callback of callbacks) callback(now);
    },
  };
  assert.equal(stage.dataset.ready, 'true');
  result.advance(0);
  return result;
}

test('an active pulse resumes after WebGL restoration without pointer hover', () => {
  const page = fixture();
  page.stage.emit('click'); // Native button activation also covers keyboard/touch.
  page.advance(100);
  const ageBeforeLoss = page.states()[0].age;
  page.canvas.emit('webglcontextlost', { preventDefault() {} });
  assert.equal(page.image.style.opacity, '1');
  assert.equal(page.stage.dataset.ready, 'false');
  page.advance(200);
  page.canvas.emit('webglcontextrestored');
  assert.equal(page.stage.dataset.ready, 'true');
  assert.equal(page.image.style.opacity, '0');
  page.advance(300);
  assert.ok(page.states()[0].age > ageBeforeLoss, 'the restored signal must progress');
  page.advance(5000);
  assert.equal(page.stage.dataset.activePulseCount, '0');
  assert.equal(page.stage.dataset.completedPulseCount, '1');
  assert.equal(page.pendingFrames(), 0);
});

test('successive clicks keep their own progress and finish independently', () => {
  const page = fixture();
  page.stage.emit('click');
  page.advance(350);
  page.stage.emit('click');
  page.advance(700);
  page.stage.emit('click');
  page.advance(1000);
  const states = page.states();
  assert.deepEqual(states.map(pulse => pulse.id), [1, 2, 3]);
  assert.ok(states[0].age > states[1].age && states[1].age > states[2].age);
  page.advance(1600);
  assert.equal(page.stage.dataset.completedPulseCount, '1');
  assert.deepEqual(page.states().map(pulse => pulse.id), [2, 3]);
  page.advance(2300);
  assert.equal(page.stage.dataset.completedPulseCount, '3');
  assert.equal(page.stage.dataset.activePulseCount, '0');
  assert.equal(page.pendingFrames(), 0);
});

test('reduced motion keeps joints still and shows a brief static response', () => {
  const page = fixture({ reducedMotion: true });
  page.stage.emit('pointerenter', { pointerType: 'mouse', clientX: 500, clientY: 240 });
  page.stage.emit('click');
  page.advance(200);
  assert.equal(page.states()[0].phase, 'highlight');
  const hands = JSON.parse(page.stage.dataset.joints);
  for (const hand of Object.values(hands)) {
    assert.equal(hand.wrist.angle, 0);
    for (const finger of ['thumb', 'index', 'middle', 'ring', 'pinky']) {
      assert.ok(hand[finger].every(joint => joint.angle === 0));
    }
  }
  assert.equal(page.art.style.transform, 'none');
  page.advance(600);
  assert.equal(page.stage.dataset.completedPulseCount, '1');
  assert.equal(page.stage.dataset.activePulseCount, '0');
  assert.equal(page.pendingFrames(), 0);
});

test('the pulse reaches the far wrist before completing in 1.5 seconds', () => {
  const page = fixture();
  page.stage.emit('click');
  page.advance(650);
  assert.equal(page.states()[0].phase, 'bridge');
  page.advance(800);
  assert.equal(page.states()[0].phase, 'robot-hand');
  page.advance(1450);
  assert.equal(page.states()[0].phase, 'robot-wrist');
  page.advance(1500);
  assert.equal(page.stage.dataset.completedPulseCount, '1');
  assert.equal(page.stage.dataset.activePulseCount, '0');
  assert.equal(page.pendingFrames(), 0);
});
