import express from 'express';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const port = Number(process.env.PORT || 8000);
const host = process.env.HOST || '0.0.0.0';
const pythonBin = process.env.PYTHON_BIN || path.join(__dirname, '.venv', 'bin', 'python');
const maxJobs = Math.max(1, Number(process.env.MAX_JOBS || 1));
const maxQueue = Math.max(0, Number(process.env.MAX_QUEUE || 20));
const requestTimeoutMs = Math.max(10_000, Number(process.env.REQUEST_TIMEOUT_MS || 900_000));
const maxBodyBytes = process.env.MAX_BODY_BYTES || '16kb';
const apiKey = process.env.SERV_API_API || process.env.SERV_API_KEY || '';
const resultCacheTtlMs = Math.max(0, Number(process.env.RESULT_CACHE_TTL_MS || 300_000));
const resultCacheMax = Math.max(1, Number(process.env.RESULT_CACHE_MAX || 50));

let activeJobs = 0;
const pendingJobs = [];
const jobsByKey = new Map();
const resultCache = new Map();

app.use(express.json({ limit: maxBodyBytes }));

app.get('/health', (_req, res) => {
  res.json({
    ok: true,
    status: 'ready',
    service: 'clothing-analyzer-express-api',
    active_jobs: activeJobs,
    queued_jobs: pendingJobs.length,
    coalesced_jobs: jobsByKey.size,
    cached_results: resultCache.size,
    max_jobs: maxJobs,
    max_queue: maxQueue,
  });
});

app.use('/analyze', (req, res, next) => {
  if (!apiKey) {
    next();
    return;
  }
  const provided = req.get('x-api-key') || '';
  if (provided !== apiKey) {
    res.status(401).json({ ok: false, status: 'unauthorized', error: 'valid x-api-key header is required' });
    return;
  }
  next();
});

app.post('/analyze', async (req, res) => {
  const requestId = crypto.randomUUID();
  const payload = req.body || {};
  if (!payload.url || typeof payload.url !== 'string') {
    console.warn(`[${requestId}] rejected: missing url`);
    res.status(400).json({ ok: false, status: 'bad_request', request_id: requestId, error: 'url is required' });
    return;
  }

  const jobKey = analysisJobKey(payload);
  const cached = getCachedResult(jobKey);
  if (cached) {
    const body = cloneJson(cached.result);
    body.request_id = requestId;
    body.queue_ms = 0;
    body.cached_response = true;
    body.cache_age_ms = Math.round(performance.now() - cached.createdAt);
    console.log(`[${requestId}] analyze cache hit url=${payload.url}`);
    res.status(body.ok ? 200 : 502).json(body);
    return;
  }

  const existingJob = jobsByKey.get(jobKey);
  if (existingJob) {
    existingJob.responders.push({ requestId, res, enqueuedAt: performance.now() });
    console.log(`[${requestId}] analyze coalesced url=${payload.url} responders=${existingJob.responders.length}`);
    return;
  }

  if (activeJobs >= maxJobs && pendingJobs.length >= maxQueue) {
    console.warn(`[${requestId}] rejected: analyzer queue full`);
    res.status(429).json({ ok: false, status: 'queue_full', request_id: requestId, error: 'analyzer queue is full; retry shortly' });
    return;
  }

  const enqueuedAt = performance.now();
  const job = { jobKey, payload, responders: [{ requestId, res, enqueuedAt }] };
  pendingJobs.push(job);
  jobsByKey.set(jobKey, job);
  console.log(`[${requestId}] analyze queued url=${payload.url} active_jobs=${activeJobs}/${maxJobs} queued_jobs=${pendingJobs.length}/${maxQueue}`);
  processQueue();
});

app.use((err, _req, res, _next) => {
  if (err?.type === 'entity.too.large') {
    res.status(413).json({ ok: false, status: 'too_large', error: `request body must be <= ${maxBodyBytes}` });
    return;
  }
  res.status(400).json({ ok: false, status: 'bad_json', error: err.message });
});

function processQueue() {
  while (activeJobs < maxJobs && pendingJobs.length > 0) {
    const job = pendingJobs.shift();
    runQueuedJob(job);
  }
}

async function runQueuedJob(job) {
  activeJobs += 1;
  const started = performance.now();
  const primary = job.responders[0];
  const queueMs = Math.round((started - primary.enqueuedAt) * 100) / 100;
  console.log(`[${primary.requestId}] analyze started url=${job.payload.url} active_jobs=${activeJobs}/${maxJobs} queue_ms=${queueMs} responders=${job.responders.length}`);
  try {
    const result = await analyzeWithPython(job.payload);
    result.elapsed_ms = Math.round((performance.now() - started) * 100) / 100;
    setCachedResult(job.jobKey, result);
    console.log(`[${primary.requestId}] analyze finished ok=${result.ok} elapsed_ms=${result.elapsed_ms} responders=${job.responders.length}`);
    sendJobResult(job, result, started);
  } catch (error) {
    const elapsedMs = Math.round((performance.now() - started) * 100) / 100;
    console.error(`[${primary.requestId}] analyze failed status=${error.status || 'error'} elapsed_ms=${elapsedMs} error=${error.message}`);
    sendJobError(job, error, started, elapsedMs);
  } finally {
    jobsByKey.delete(job.jobKey);
    activeJobs -= 1;
    processQueue();
  }
}

function sendJobResult(job, result, started) {
  for (const responder of job.responders) {
    const body = cloneJson(result);
    body.request_id = responder.requestId;
    body.queue_ms = Math.round((started - responder.enqueuedAt) * 100) / 100;
    if (job.responders.length > 1 && responder !== job.responders[0]) {
      body.coalesced_request = true;
    }
    responder.res.status(body.ok ? 200 : 502).json(body);
  }
}

function sendJobError(job, error, started, elapsedMs) {
  for (const responder of job.responders) {
    responder.res.status(error.statusCode || 500).json({
      ok: false,
      status: error.status || 'error',
      request_id: responder.requestId,
      elapsed_ms: elapsedMs,
      queue_ms: Math.round((started - responder.enqueuedAt) * 100) / 100,
      error: error.message,
    });
  }
}

function analyzeWithPython(payload) {
  return new Promise((resolve, reject) => {
    const args = [
      path.join(__dirname, 'youtube_frame_fetcher.py'),
      '--url',
      payload.url,
      '--analyze-clothing',
      '--analysis-width',
      String(intValue(payload.analysis_width, 384)),
      '--max-height',
      String(intValue(payload.max_height, 480)),
      '--min-vote-frames',
      String(intValue(payload.min_vote_frames, 7)),
      '--seconds',
      secondsValue(payload.seconds, '5,10,15,20,30,45,60'),
      '--auto-seconds',
      secondsValue(payload.auto_seconds, '5,10,15,20,30,45,60,75,90,120'),
    ];

    if (payload.no_auto_sample) {
      args.push('--no-auto-sample');
    }
    if (payload.skip_video) {
      args.push('--skip-video');
    }
    if (payload.query && typeof payload.query === 'string') {
      args.push('--query', payload.query);
    }

    const child = spawn(pythonBin, args, {
      cwd: __dirname,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      const error = new Error('analysis timed out');
      error.statusCode = 504;
      error.status = 'timeout';
      reject(error);
    }, requestTimeoutMs);

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        const error = new Error(stderr.trim() || `python analyzer exited with code ${code}`);
        error.statusCode = 502;
        error.status = 'analyzer_failed';
        reject(error);
        return;
      }

      try {
        resolve(JSON.parse(stdout));
      } catch (_error) {
        const error = new Error('python analyzer returned invalid JSON');
        error.statusCode = 502;
        error.status = 'invalid_analyzer_json';
        error.details = stdout.slice(-1000);
        reject(error);
      }
    });
  });
}

function intValue(value, fallback) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return parsed;
}

function secondsValue(value, fallback) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.map((item) => Number.parseInt(item, 10)).filter(Number.isFinite).join(',');
  }
  const normalized = String(value)
    .split(',')
    .map((item) => Number.parseInt(item.trim(), 10))
    .filter(Number.isFinite)
    .join(',');
  return normalized || fallback;
}

function analysisJobKey(payload) {
  return JSON.stringify({
    url: payload.url,
    query: typeof payload.query === 'string' ? payload.query : '',
    analysis_width: intValue(payload.analysis_width, 384),
    max_height: intValue(payload.max_height, 480),
    min_vote_frames: intValue(payload.min_vote_frames, 7),
    seconds: secondsValue(payload.seconds, '5,10,15,20,30,45,60'),
    auto_seconds: secondsValue(payload.auto_seconds, '5,10,15,20,30,45,60,75,90,120'),
    no_auto_sample: Boolean(payload.no_auto_sample),
    skip_video: Boolean(payload.skip_video),
  });
}

function getCachedResult(jobKey) {
  if (resultCacheTtlMs <= 0) {
    return null;
  }
  const cached = resultCache.get(jobKey);
  if (!cached) {
    return null;
  }
  if (performance.now() - cached.createdAt > resultCacheTtlMs) {
    resultCache.delete(jobKey);
    return null;
  }
  return cached;
}

function setCachedResult(jobKey, result) {
  if (resultCacheTtlMs <= 0 || !result?.ok) {
    return;
  }
  resultCache.set(jobKey, { createdAt: performance.now(), result: cloneJson(result) });
  pruneResultCache();
}

function pruneResultCache() {
  const now = performance.now();
  for (const [key, cached] of resultCache.entries()) {
    if (now - cached.createdAt > resultCacheTtlMs) {
      resultCache.delete(key);
    }
  }
  while (resultCache.size > resultCacheMax) {
    const oldestKey = resultCache.keys().next().value;
    resultCache.delete(oldestKey);
  }
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

app.listen(port, host, () => {
  console.log(`Clothing analyzer Express API listening on http://${host}:${port}`);
});
