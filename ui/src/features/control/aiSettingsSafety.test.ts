import { expect, it } from 'vitest';
import { aiRebuildPrompt, redactAiSaveError } from './aiSettingsSafety';
it('requires confirmation for model, corpus and pending rebuild changes with existing entries', () => {
  const current = { embeddings: { model_id: 'old' }, index: { counts: { chunks: 12 }, corpus: { tasks: true } } };
  expect(aiRebuildPrompt(current, { ai_embedding_model: 'new', ai_index_corpus: {} })).toContain('12 entries');
  expect(aiRebuildPrompt(current, { ai_embedding_model: 'old', ai_index_corpus: { tasks: false } })).toContain('12 entries');
  expect(aiRebuildPrompt(current, { ai_embedding_model: 'old', ai_index_corpus: {} })).toBeNull();
  expect(aiRebuildPrompt({ ...current, index: { rebuild_warning: { required: true, estimated_entries: 4 } } }, { ai_embedding_model: 'old' })).toContain('4 entries');
});
it('matches Classic estimate fallbacks and never confirms an empty index', () => {
  expect(aiRebuildPrompt({ embeddings: { model_id: 'old' }, index: { counts: { sources: 3 } } }, { ai_embedding_model: 'new' })).toContain('3 entries');
  for (const count of [0, -1, 'nope', Infinity]) expect(aiRebuildPrompt({ embeddings: { model_id: 'old' }, index: { counts: { chunks: count }, rebuild_warning: { required: true } } }, { ai_embedding_model: 'new' })).toBeNull();
});
it('redacts exact and trimmed keys, metacharacters and labeled credentials', () => {
  expect(redactAiSaveError('Rejected abc.[] abc.[]more password=hidden authorization:bearer', { a: ' abc.[] ', b: 'abc.[]more' })).toBe('Rejected [redacted] [redacted] password: [redacted] authorization: [redacted]');
});
