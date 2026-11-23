// throwFromResponse: the error message a caller shows and the `retryable`
// flag its poller/retry logic branches on (a permanent 4xx must stop polling).
import { beforeEach, test, vi } from 'vitest';
import assert from 'node:assert/strict';

vi.mock('./apiToken', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from './apiToken';
import { submitBatchJob, submitProcessJob, throwFromResponse } from './jobs.js';

beforeEach(() => {
  vi.clearAllMocks();
  apiFetch.mockResolvedValue({ ok: true, json: async () => ({ job_id: 'job-1', jobs: [], total: 0 }) });
});

const response = (status, body) => ({ status, text: async () => body });

async function errorFor(status, body) {
  try {
    await throwFromResponse(response(status, body));
  } catch (error) {
    return error;
  }
  throw new Error('throwFromResponse did not throw');
}

test('single-job submission omits an empty Gemini header so the backend can use its saved key', async () => {
  await submitProcessJob({ type: 'url', payload: 'https://example.com/video' }, '');

  const [, options] = apiFetch.mock.calls[0];
  assert.equal(options.headers['X-Gemini-Key'], undefined);
  assert.equal(options.headers['Content-Type'], 'application/json');
});

test('batch submission omits an empty Gemini header so the backend can use its saved key', async () => {
  await submitBatchJob({ urls: ['https://example.com/video'] }, '');

  const [, options] = apiFetch.mock.calls[0];
  assert.equal(options.headers['X-Gemini-Key'], undefined);
  assert.equal(options.headers['Content-Type'], 'application/json');
});

test('retryable only for timeouts, rate limits and server errors', async () => {
  for (const status of [408, 429, 500, 502, 503, 504]) {
    const error = await errorFor(status, '');
    assert.equal(error.status, status);
    assert.equal(error.retryable, true, `HTTP ${status}`);
  }
  for (const status of [400, 401, 403, 404, 409, 413, 422]) {
    assert.equal((await errorFor(status, '')).retryable, false, `HTTP ${status}`);
  }
});

test('message comes from FastAPI detail, then message, then the raw text', async () => {
  assert.equal((await errorFor(409, '{"detail":"Job is still active"}')).message,
    'Job is still active');
  assert.equal((await errorFor(429, '{"message":"Daily limit reached"}')).message,
    'Daily limit reached');
  assert.equal((await errorFor(422, '{"detail":[{"loc":["body","url"],"msg":"bad"}]}')).message,
    '[{"loc":["body","url"],"msg":"bad"}]');
  assert.equal((await errorFor(502, '<html>Bad Gateway</html>')).message,
    '<html>Bad Gateway</html>');
  assert.equal((await errorFor(503, '{"other":1}')).message, '{"other":1}');
});

test('empty body falls back to the HTTP status', async () => {
  assert.equal((await errorFor(500, '')).message, 'HTTP 500');
});
