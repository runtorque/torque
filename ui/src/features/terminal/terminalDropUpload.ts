export const terminalDropUploadTimeout = 30_000;
interface UploadResult { path?: string; error?: string }
const record = (value: unknown): Record<string, unknown> | null => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;

/** Each file settles independently so one failed image cannot discard the others. */
function uploadImage(taskId: string, file: File, signal: AbortSignal): Promise<UploadResult> {
  return new Promise((resolve) => {
    const controller = new AbortController(); let settled = false;
    const finish = (result: UploadResult) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort', cancel);
      controller.abort(); resolve(result);
    };
    const cancel = () => finish({ error: 'Upload cancelled.' });
    const timer = setTimeout(() => finish({ error: 'Image upload timed out.' }), terminalDropUploadTimeout);
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) { cancel(); return; }
    const upload = async (): Promise<UploadResult> => {
      const body = new FormData(); body.append('task_id', taskId); body.append('file', file);
      const response = await fetch('/api/upload', { method: 'POST', body, signal: controller.signal });
      const payload = record(await response.json());
      if (!response.ok || payload?.ok !== true) throw new Error(typeof payload?.error === 'string' && payload.error ? payload.error : 'Image upload failed.');
      const entry = Array.isArray(payload.data) && payload.data.length === 1 ? record(payload.data[0]) : null;
      // Never coerce an untrusted descriptor into terminal input.
      if (!entry || typeof entry.path !== 'string' || !entry.path.trim() || [...entry.path].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) throw new Error('Invalid image upload acknowledgement.');
      return { path: entry.path };
    };
    // Observation includes response parsing and does not depend on abort support.
    void upload().then(finish, (cause: unknown) => finish({ error: cause instanceof Error ? cause.message : 'Image upload failed.' }));
  });
}

export async function uploadTerminalImages(taskId: string, files: File[], signal: AbortSignal) {
  const results = await Promise.all(files.map((file) => uploadImage(taskId, file, signal)));
  return {
    paths: results.flatMap((result) => result.path ? [result.path] : []),
    failures: results.flatMap((result, index) => result.error ? [`${files[index]!.name}: ${result.error}`] : []),
  };
}
