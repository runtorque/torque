import { readCommand } from '../protocol/http';

const WINDOW_MS = 5_000;
const SAMPLE_LIMIT = 240;
export const RENDER_REPORT_MS = 2_000;
const REQUEST_TIMEOUT_MS = 10_000;
interface RenderSample { at: number; duration: number }
export interface RenderStats { render_per_s: number; render_ms_p95: number }

/** Actual committed React render work, with the same bounded window as Classic. */
export class RenderWindow {
  private samples: RenderSample[] = [];
  private sampled = false;

  record(duration: number, at: number) {
    if (!Number.isFinite(at)) return;
    this.sampled = true;
    this.samples.push({ at, duration: Number.isFinite(duration) ? Math.max(0, duration) : 0 });
    this.prune(at);
  }

  private prune(now: number) {
    this.samples = this.samples.filter((sample) => sample.at >= now - WINDOW_MS).slice(-SAMPLE_LIMIT);
  }

  stats(now: number): RenderStats | null {
    if (!this.sampled) return null;
    this.prune(now);
    const first = this.samples[0];
    if (!first) return { render_per_s: 0, render_ms_p95: 0 };
    const last = this.samples[this.samples.length - 1]!;
    const elapsed = Math.max(1, (Math.max(now, last.at) - first.at) / 1_000);
    const durations = this.samples.map((sample) => sample.duration).sort((a, b) => a - b);
    return { render_per_s: this.samples.length / elapsed, render_ms_p95: durations[Math.ceil(durations.length * 0.95) - 1]! };
  }
}

/** HTTP acknowledgements never enter Redux or produce a report/render feedback loop. */
export function startRenderReports(samples: RenderWindow) {
  let pending: AbortController | null = null;
  let timeout: number | undefined;
  const timer = window.setInterval(() => {
    if (pending) return;
    const stats = samples.stats(performance.now());
    if (!stats) return;
    const controller = new AbortController();
    pending = controller;
    timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    void readCommand({ cmd: 'report_frontend_render', ...stats }, controller.signal)
      .catch(() => undefined) // Optional telemetry must never interrupt operator work.
      .finally(() => { window.clearTimeout(timeout); timeout = undefined; pending = null; });
  }, RENDER_REPORT_MS);
  return () => { window.clearInterval(timer); window.clearTimeout(timeout); pending?.abort(); };
}
