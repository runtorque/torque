import { describe, expect, it } from 'vitest';
import { composerCompletion, insertCompletion, type CompletionScope } from './completionModel';
const catalog = [
  { id: 'compact', label: '/compact', insert: '/compact', help: 'Compact context' },
  { id: 'fast', label: '/fast', insert: '/fast', providers: ['codex'] },
  { id: 'loop', label: '/loop every <interval> <message>', insert: '/loop every 10m ', usage: '/loop every 10m check status', search: 'recurring schedule' },
];
const scope: CompletionScope = { catalog, tasks: { 'T:1': { group: 'one', task: 'Fix renderer' }, 'T:2': { group: 'two', task: 'Fix other' }, 'T:3': { group: 'one', task: 'Fix archived', archived_at: 20 }, 'T:4': { group: 'one', task: 'Fix archived lane', lane: 'Archived' } }, group: 'one', provider: 'generic', hasTarget: true };
describe('composer completion contracts', () => {
  it('uses only valid catalog records for the target provider and searches usage and metadata', () => {
    expect(composerCompletion('/', [1, 1], scope)?.items.map((item) => item.id)).toEqual(['compact', 'loop']);
    expect(composerCompletion('/', [1, 1], { ...scope, provider: ' CODEX ' })?.items.map((item) => item.id)).toEqual(['compact', 'fast', 'loop']);
    expect(composerCompletion('/recurring', [10, 10], scope)?.items[0]?.insert).toBe('/loop every 10m ');
    expect(composerCompletion('/status', [7, 7], scope)?.items[0]?.id).toBe('loop');
    for (const value of [undefined, {}, [null, {}, { id: 'empty' }]]) expect(composerCompletion('/', [1, 1], { ...scope, catalog: value })).toBeNull();
  });
  it('does not interpret ordinary prose, second-line slashes, selected text or standalone-terminal input', () => {
    for (const value of ['Explain /compact', '\n/compact', '/unknown prose']) expect(composerCompletion(value, [value.length, value.length], scope)).toBeNull();
    expect(composerCompletion('/compact', [0, 8], scope)).toBeNull(); expect(composerCompletion('/', [1, 1], { ...scope, hasTarget: false })).toBeNull();
  });
  it('scopes references, excludes archives and retains surrounding text and insertion caret', () => {
    const value = 'Please :rend after'; const completion = composerCompletion(value, [12, 12], scope)!;
    expect(completion.items.map((item) => item.id)).toEqual(['T:1']); expect(insertCompletion(value, completion, completion.items[0]!)).toEqual({ text: 'Please T:1  after', selection: [11, 11] });
    expect(composerCompletion('time:rend', [9, 9], scope)).toBeNull(); expect(composerCompletion(':', [1, 1], { ...scope, group: '' })).toBeNull();
    expect(composerCompletion(':', [1, 1], { ...scope, hasTarget: false })?.items[0]?.id).toBe('T:1');
    const tasks = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`T:${i}`, { group: 'one' }])); expect(composerCompletion(':', [1, 1], { ...scope, tasks })?.items).toHaveLength(8);
  });
  it('inserts the exact server template, including trailing spaces, without executing it', () => {
    const completion = composerCompletion('/loop', [5, 5], scope)!; expect(insertCompletion('/loop', completion, completion.items[0]!)).toEqual({ text: '/loop every 10m ', selection: [16, 16] });
  });
});
