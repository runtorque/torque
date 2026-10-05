export function taskText(value: unknown, fallback = '') { return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback; }
export const artifactTypes = ['image', 'file_ref', 'snippet', 'log', 'diff', 'test_report', 'generated_doc'];
export function defaultPrompt(type: string) { return type === 'image' || type === 'file_ref' ? 'path' : type === 'snippet' ? 'inline' : 'summary'; }
export function uploadType(filename: string, mime: string) {
  if (mime.startsWith('image/')) return 'image';
  if (/\.(diff|patch)$/i.test(filename)) return 'diff';
  if (/(^|[._-])(pytest|junit|tap|coverage|report|results?)([._-]|$)/i.test(filename)) return 'test_report';
  if (/\.(log|out|err|trace|txt)$/i.test(filename)) return 'log';
  if (/\.(md|markdown|html?|json|ya?ml|xml|csv)$/i.test(filename) || /^(text\/)|json|xml/.test(mime)) return 'generated_doc';
  return 'file_ref';
}
