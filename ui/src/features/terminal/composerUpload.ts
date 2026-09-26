import type { ComposerAttachment } from './composerState';

export const composerUploadTimeout = 30_000;
const record = (value: unknown): Record<string, unknown> | null => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const invalidUpload = () => new Error('The daemon returned an invalid upload acknowledgement. Your draft is retained; attach the image again to retry.');

/** The source draft owns this operation even while its composer is offscreen. */
export function uploadComposerImages(cellId: string, files: File[]): Promise<ComposerAttachment[]> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController(); let settled = false;
    const finish = (attachments?: ComposerAttachment[], error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (error) { controller.abort(); reject(error); } else resolve(attachments!);
    };
    // Abort alone cannot bound a stalled response body or an adapter that ignores
    // cancellation. Expired results must not reach the caller or create previews.
    const timer = setTimeout(() => finish(undefined, new Error('Image upload timed out. Your draft is retained; attach the image again to retry.')), composerUploadTimeout);
    const upload = async () => {
      const body = new FormData(); body.append('agent_id', cellId);
      files.forEach((file) => body.append('file', file));
      const response = await fetch('/api/attachment/upload', { method: 'POST', body, signal: controller.signal });
      const payload = record(await response.json());
      if (!payload) throw invalidUpload();
      if (!response.ok || payload.ok !== true) throw new Error(typeof payload.error === 'string' && payload.error ? payload.error : 'Image upload failed. Your draft is retained.');
      if (!Array.isArray(payload.data) || payload.data.length !== files.length || !files.length) throw invalidUpload();
      return payload.data.map((value): ComposerAttachment => {
        const entry = record(value);
        if (!entry || typeof entry.path !== 'string' || !entry.path.trim() || entry.path.includes('\0') || typeof entry.filename !== 'string' || !entry.filename.trim()) throw invalidUpload();
        if (entry.mime_type !== undefined && typeof entry.mime_type !== 'string') throw invalidUpload();
        if (entry.size_bytes !== undefined && (typeof entry.size_bytes !== 'number' || !Number.isSafeInteger(entry.size_bytes) || entry.size_bytes < 0)) throw invalidUpload();
        return { path: entry.path, filename: entry.filename, ...(entry.mime_type === undefined ? {} : { mime_type: entry.mime_type }), ...(entry.size_bytes === undefined ? {} : { size_bytes: entry.size_bytes }) };
      });
    };
    void upload().then((attachments) => finish(attachments), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Image upload failed. Your draft is retained.')));
  });
}
