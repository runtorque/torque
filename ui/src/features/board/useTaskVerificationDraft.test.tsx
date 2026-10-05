import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import { useTaskVerificationDraft } from './useTaskVerificationDraft';
const original = { mode: 'deploy', state: 'pending', notes: 'Saved note', summary: { tests_run: 'original suite', manual_smoke_done: false } };
it('keeps each explicit summary edit while refreshing untouched keys and supports an intentional reversal after a saved change', () => {
  const { result, rerender } = renderHook(useTaskVerificationDraft, { initialProps: original });
  act(() => { result.current.edit({ ...result.current.draft, summary: { ...result.current.draft.summary, tests_run: 'Draft suite' } }); });
  rerender({ ...original, state: 'passed', summary: { tests_run: 'Remote suite', manual_smoke_done: true } });
  expect(result.current.draft).toMatchObject({ state: 'passed', summary: { tests_run: 'Draft suite', manual_smoke_done: true } });
  act(() => { result.current.edit({ ...result.current.draft, state: 'pending' }); });
  rerender({ ...original, state: 'passed', notes: 'Remote note', summary: { tests_run: 'Remote suite', manual_smoke_done: true } });
  expect(result.current.draft).toMatchObject({ state: 'pending', notes: 'Remote note' }); expect(result.current.baseline.state).toBe('passed');
});
it('advances only authored fields after save when newer verification evidence arrived during the pending request', () => {
  const { result, rerender } = renderHook(useTaskVerificationDraft, { initialProps: original });
  act(() => { result.current.edit({ ...result.current.draft, notes: 'Submitted note' }); });
  const before = result.current.baseline; const submitted = result.current.draft;
  rerender({ ...original, state: 'passed', notes: 'Submitted note', summary: { tests_run: 'Newer suite', manual_smoke_done: true } });
  act(() => { result.current.saved(submitted, before); });
  expect(result.current.baseline).toEqual(result.current.draft); expect(result.current.baseline.summary.tests_run).toBe('Newer suite');
  rerender({ ...original, state: 'passed', notes: 'Later operator note', summary: { tests_run: 'Latest suite', manual_smoke_done: true } });
  expect(result.current.draft.notes).toBe('Later operator note'); expect(result.current.baseline).toEqual(result.current.draft);
});
