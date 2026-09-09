// Run with: node tests/test_streaming_client.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../src/london_monitor/web/app.js', import.meta.url), 'utf8');
const start = source.indexOf('async function streamChat(');
const end = source.indexOf("\n$('#stop')", start);
const events = [];
let wire = '';
const streamChat = new Function('fetch', `${source.slice(start, end)}; return streamChat;`)(
  async () => new Response(new ReadableStream({
    start(controller) {
      // Split every UTF-8 character and frame boundary across separate network chunks.
      for (const byte of new TextEncoder().encode(wire)) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    },
  })),
);
wire = ': keep-alive\n\ndata: {"type":"activity","message":"Checking £95"}\n\n'
  + 'data: {"type":"result","data":{"answer":"Verified £95"}}\n\n';
assert.deepEqual(await streamChat({}, undefined, message => events.push(message)), {
  answer: 'Verified £95',
});
assert.deepEqual(events, ['Checking £95']);
wire = 'data: {"type":"activity","message":"Working"}\n\n';
await assert.rejects(streamChat({}, undefined, () => {}), /ended before an answer/);
wire = 'data: {"type":"error","message":"Provider unavailable"}\n\n';
await assert.rejects(streamChat({}, undefined, () => {}), /Provider unavailable/);
console.log('Streaming client checks passed.');
