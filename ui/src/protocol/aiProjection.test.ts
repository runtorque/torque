import { expect, it } from 'vitest';
import { createAppStore, projectionActions, selectOperationsState } from '../app/store';
import { compactStateFixture } from './fixtures';
import type { DeltaOperation } from './types';
function setup() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
  const emit = (op: DeltaOperation) => store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: store.getState().projection.seq + 1, ops: [op] }));
  const ai = () => selectOperationsState(store.getState()).aiSettings;
  emit({ op: 'ai_settings_update', schema_version: 1, changed_keys: [], settings: { enabled: false, generation: { provider: 'anthropic' }, embeddings: { model_id: 'desired', dependency: { status: 'available' } }, index: { corpus: { tasks: true }, counts: { chunks: 10, pending: 7 }, rebuild_warning: { required: true, reason: 'model changed' } }, boot_summary: { enabled: true, counts: { ready: 3, errors: 2 } }, metering: { calls_24h: 5, input_tokens_24h: 7 } } });
  return { store, emit, ai };
}
it('unwraps full AI settings updates into the actual operations selector', () => {
  const { ai, emit } = setup(); expect(ai()).toMatchObject({ enabled: false, embeddings: { model_id: 'desired' } }); expect(ai()).not.toHaveProperty('settings'); expect(ai()).not.toHaveProperty('schema_version');
  emit({ op: 'ai_settings_update', enabled: true, schema_version: 1 }); expect(ai()).toEqual({ enabled: true });
});
it('merges index and embedding partial deltas without dropping settings or nested counts', () => {
  const { ai, emit } = setup(); const generation = ai().generation;
  emit({ op: 'ai_index_status_update', schema_version: 1, index: { status: 'building', counts: { pending: 4 }, job: { id: 'j', status: 'running' }, rebuild_warning: { required: false } }, embeddings: { active_model_id: 'active', active_dims: 1024 } });
  expect(ai()).toMatchObject({ index: { status: 'building', corpus: { tasks: true }, counts: { chunks: 10, pending: 4 }, current_job: { id: 'j', status: 'running' }, rebuild_warning: { required: false, reason: 'model changed' } }, embeddings: { model_id: 'desired', active_model_id: 'active', active_dims: 1024, dependency: { status: 'available' } } }); expect(ai().generation).toBe(generation);
  emit({ op: 'ai_index_status_update', index: { current_job: null, last_error: '' } }); expect(ai().index).toMatchObject({ current_job: null, counts: { chunks: 10 } });
});
it('merges summary counts and metering separately, including zero and cleared values', () => {
  const { ai, emit } = setup(); emit({ op: 'ai_summary_status_update', boot_summary: { status: 'ready', counts: { errors: 0 }, last_error: '' }, metering: { calls_24h: 0, output_tokens_24h: 99 } });
  expect(ai()).toMatchObject({ boot_summary: { enabled: true, status: 'ready', counts: { ready: 3, errors: 0 }, last_error: '' }, metering: { calls_24h: 0, input_tokens_24h: 7, output_tokens_24h: 99 } });
  emit({ op: 'ai_summary_status_update', status: 'stale', counts: { stale: 2 }, changed_keys: ['status'] }); expect(ai().boot_summary).toMatchObject({ status: 'stale', counts: { ready: 3, errors: 0, stale: 2 } }); expect(ai().boot_summary).not.toHaveProperty('changed_keys');
});
it.each(['ai_index_status_update', 'ai_summary_status_update'] as const)('accepts complete settings carried by %s', (op) => {
  const { ai, emit } = setup(); emit({ op, settings: { enabled: true, index: { status: 'ready' } } }); expect(ai()).toEqual({ enabled: true, index: { status: 'ready' } });
});
