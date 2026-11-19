// Test the real meeting component with mocked devices and room data delivery.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'frontend/src/main.tsx'), 'utf8');
const compiled = ts.transpileModule(source + '\nexport { GroupMeeting };', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
}).outputText;

function setup(micError) {
  const values = [], effects = [], calls = [];
  let index = 0, receive;
  const room = {
    localParticipant: {
      identity: 'human_test', isMicrophoneEnabled: false, isCameraEnabled: true,
      async setMicrophoneEnabled(enabled) {
        calls.push(['microphone', enabled]);
        if (micError) throw new Error(micError);
      },
      async publishData(bytes, options) {
        calls.push(['data', JSON.parse(new TextDecoder().decode(bytes)), options]);
      },
    },
    on(event, callback) { if (event === 'data') receive = callback; },
    off() {},
  };
  const react = {
    createElement(type, props, ...children) { return { type, props: props || {}, children }; },
    useState(initial) {
      const slot = index++;
      values[slot] = initial;
      return [initial, value => { values[slot] = value; }];
    },
    useMemo(fn) { return fn(); },
    useEffect(fn) { effects.push(fn); },
  };
  const context = {
    exports: {}, TextEncoder, TextDecoder, URL, Error,
    window: { location: { origin: 'http://localhost:5173' } },
    document: { getElementById: () => ({}) },
    require(name) {
      if (name === 'react') return react;
      if (name === 'react-dom/client') return { createRoot: () => ({ render() {} }) };
      if (name === '@livekit/components-react') return { useRoomContext: () => room, useParticipants: () => [] };
      if (name === 'livekit-client') return { RoomEvent: { DataReceived: 'data' } };
      return {};
    },
  };
  // Vite substitutes import.meta.env; supply equivalent empty settings here.
  vm.runInNewContext(compiled.replaceAll('import.meta.env', '({})'), context);
  const tree = context.exports.GroupMeeting({ meetingId: 'test', inviteCode: 'invite', accessToken: 'token', onLeave() {} });
  effects.forEach(fn => fn());
  function findButton(node) {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'button' && node.children.some(value => typeof value === 'string' && value.includes('Talk to Mateo'))) return node;
    return node.children?.map(findButton).find(Boolean);
  }
  return { calls, values, talk: findButton(tree).props.onClick,
    acknowledge(topic = 'mateo-floor') {
      receive(new TextEncoder().encode(JSON.stringify({ type: 'floor_changed', participant_id: 'human_test' })), undefined, undefined, topic);
    } };
}

(async () => {
  const meeting = setup();
  meeting.talk();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(JSON.parse(JSON.stringify(meeting.calls)), [
    ['microphone', true],
    ['data', { type: 'request_floor' }, { reliable: true, topic: 'mateo-floor' }],
  ]);
  assert.equal(meeting.values[6], '', 'speaker must wait for acknowledgment');
  meeting.acknowledge('other-topic');
  assert.equal(meeting.values[6], '', 'unrelated messages must be ignored');
  meeting.acknowledge();
  assert.equal(meeting.values[6], 'human_test');
  assert.match(meeting.values[4], /listening to you/);

  const denied = setup('Microphone permission denied');
  denied.talk();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(denied.calls.length, 1, 'failed microphone must not claim the floor');
  assert.match(denied.values[4], /permission denied/);
  console.log('PASS: microphone activation, floor request, acknowledgment, topic filter, permission error');
})().catch(error => { console.error(error); process.exitCode = 1; });
