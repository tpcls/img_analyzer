import express from 'express';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const port = Number(process.env.PORT || 8000);
const host = process.env.HOST || '0.0.0.0';
const pythonBin = process.env.PYTHON_BIN || path.join(__dirname, '.venv', 'bin', 'python');
const maxJobs = Math.max(1, Number(process.env.MAX_JOBS || 1));
const requestTimeoutMs = Math.max(10_000, Number(process.env.REQUEST_TIMEOUT_MS || 900_000));
const maxBodyBytes = process.env.MAX_BODY_BYTES || '16kb';
const apiKey = process.env.API_KEY || '';

let activeJobs = 0;

app.use(express.json({ limit: maxBodyBytes }));

app.get('/health', (_req, res) => {
  res.json({
    ok: true,
    status: 'ready',
    service: 'clothing-analyzer-express-api',
    active_jobs: activeJobs,
    max_jobs: maxJobs,
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
  if (activeJobs >= maxJobs) {
    res.status(429).json({ ok: false, status: 'busy', error: 'analyzer is busy; retry shortly' });
    return;
  }

  const payload = req.body || {};
  if (!payload.url || typeof payload.url !== 'string') {
    res.status(400).json({ ok: false, status: 'bad_request', error: 'url is required' });
    return;
  }

  activeJobs += 1;
  const started = performance.now();
  try {
    const result = await analyzeWithPython(payload);
    result.elapsed_ms = Math.round((performance.now() - started) * 100) / 100;
    res.status(result.ok ? 200 : 502).json(result);
  } catch (error) {
    res.status(error.statusCode || 500).json({
      ok: false,
      status: error.status || 'error',
      error: error.message,
    });
  } finally {
    activeJobs -= 1;
  }
});

app.use((err, _req, res, _next) => {
  if (err?.type === 'entity.too.large') {
    res.status(413).json({ ok: false, status: 'too_large', error: `request body must be <= ${maxBodyBytes}` });
    return;
  }
  res.status(400).json({ ok: false, status: 'bad_json', error: err.message });
});

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
  return String(value);
}

app.listen(port, host, () => {
  console.log(`Clothing analyzer Express API listening on http://${host}:${port}`);
});
