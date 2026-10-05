import type { LogPage } from './logModel';

/** Bound the whole read even when an adapter or response body ignores abort. */
export function readLogPage(url: string, signal: AbortSignal): Promise<LogPage> {
  return new Promise((resolve, reject) => {
    const transport = new AbortController();
    let settled = false;
    const finish = (page?: LogPage, error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort', abort);
      if (error) { transport.abort(); reject(error); } else resolve(page!);
    };
    const abort = () => finish(undefined, new DOMException('Log request cancelled', 'AbortError'));
    const timer = setTimeout(() => finish(undefined, new Error('Log refresh timed out')), 15_000);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    void fetch(url, { signal: transport.signal }).then(async (response) => {
      if (!response.ok) throw new Error(`Log request failed (${response.status})`);
      return await response.json() as LogPage;
    }).then((page) => finish(page), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Unable to read logs')));
  });
}
