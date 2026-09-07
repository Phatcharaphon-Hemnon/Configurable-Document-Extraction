import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

async function load(entryPoints, plugins = []) {
  const result = await build({ entryPoints, bundle: true, platform: 'node', format: 'esm', write: false, plugins });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}
const { pollUntilTerminal } = await load(['src/api/jobPolling.ts']);
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

test('polling survives a ten-minute queue and reports processing before completion', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
  let count = 0;
  const seen = [];
  const pending = pollUntilTerminal(async () => ({ job_id: 'job', status: ++count <= 130 ? 'queued' : count === 131 ? 'processing' : 'completed' }), {
    onStatus: (job) => seen.push(job.status),
  });
  await flush();
  for (let i = 0; i < 131; i++) { t.mock.timers.tick(5000); await flush(); }
  assert.equal((await pending).status, 'completed');
  assert.equal(count, 132);
  assert.deepEqual(seen.slice(-2), ['processing', 'completed']);
});

test('abort clears polling while waiting', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const controller = new AbortController();
  let count = 0;
  const pending = pollUntilTerminal(async () => { count++; return { status: 'queued' }; }, { signal: controller.signal });
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  await flush();
  controller.abort();
  await rejected;
  t.mock.timers.tick(50000);
  await flush();
  assert.equal(count, 1);
});

test('failed jobs stop immediately and status network errors propagate', async () => {
  let calls = 0;
  assert.equal((await pollUntilTerminal(async () => { calls++; return { status: 'failed' }; })).status, 'failed');
  assert.equal(calls, 1);
  await assert.rejects(pollUntilTerminal(async () => { throw new Error('offline'); }), /offline/);
});

// Exercise the hook's submission/reconnection orchestration with deterministic
// hook storage. No DOM renderer or extra test framework is needed.
const stubs = {
  react: `
    export const useCallback = (fn) => { globalThis.queueHarness.cursor++; return fn; };
    export const useState = (initial) => {
      const h = globalThis.queueHarness, index = h.cursor++;
      if (!(index in h.slots)) h.slots[index] = initial;
      return [h.slots[index], value => { h.slots[index] = typeof value === 'function' ? value(h.slots[index]) : value; }];
    };
    export const useRef = (initial) => {
      const h = globalThis.queueHarness, index = h.cursor++;
      return h.slots[index] ??= { current: initial };
    };
    export const useEffect = (effect) => {
      const h = globalThis.queueHarness, index = h.cursor++;
      if (!(index in h.slots)) h.slots[index] = effect();
    };
  `,
  '../api/client': `export const extractFiles = (...args) => globalThis.queueHarness.extract(...args);
    export const pollJobStatus = (...args) => globalThis.queueHarness.poll(...args);`,
  '../lib/toast': 'export const pushToast = () => {};',
};
const { useDocumentQueue } = await load(['src/hooks/useDocumentQueue.ts'], [{
  name: 'hook-harness', setup(builder) {
    builder.onResolve({ filter: /^(react|\.\.\/api\/client|\.\.\/lib\/toast)$/ }, args => ({ path: args.path, namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, args => ({ contents: stubs[args.path] }));
  },
}]);
function harness(extract, poll) {
  globalThis.queueHarness = { cursor: 0, slots: [], extract, poll };
  return () => { globalThis.queueHarness.cursor = 0; return useDocumentQueue(); };
}

test('status reconnection reuses the accepted job without uploading again', async () => {
  let uploads = 0, polls = 0;
  const render = harness(async () => { uploads++; return { job_id: 'accepted' }; }, async (jobId) => {
    assert.equal(jobId, 'accepted');
    if (++polls === 1) throw new Error('offline');
    return { status: 'completed', result: { documents: [] } };
  });
  render().addFiles([{ name: 'invoice.pdf' }]);
  await flush();
  const group = render().groups[0];
  assert.equal(group.jobId, 'accepted');
  assert.match(group.error, /Could not check job status/);
  render().retryGroup(group.id);
  await flush();
  assert.equal(uploads, 1);
  assert.equal(polls, 2);
  assert.equal(render().groups[0].status, 'done');
});

test('unmount aborts active polling', async () => {
  let signal;
  const render = harness(async () => ({ job_id: 'accepted' }), (_, options) => {
    signal = options.signal;
    return new Promise((_, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  });
  render().addFiles([{ name: 'invoice.pdf' }]);
  await flush();
  for (const slot of globalThis.queueHarness.slots) if (typeof slot === 'function') slot();
  await flush();
  assert.equal(signal.aborted, true);
});
