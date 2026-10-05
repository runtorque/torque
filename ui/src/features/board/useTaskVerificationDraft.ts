import { useState } from 'react';
import type { VerificationDraft } from './VerificationFields';

function reconcile(before: VerificationDraft, draft: VerificationDraft, latest: VerificationDraft): VerificationDraft {
  const summary = { ...latest.summary };
  for (const key of new Set([...Object.keys(before.summary), ...Object.keys(draft.summary)])) {
    if (JSON.stringify(before.summary[key]) !== JSON.stringify(draft.summary[key])) summary[key] = draft.summary[key];
  }
  return { mode: draft.mode === before.mode ? latest.mode : draft.mode, state: draft.state === before.state ? latest.state : draft.state, notes: draft.notes === before.notes ? latest.notes : draft.notes, summary };
}

/** Reconcile persisted fields without turning live updates into authored edits. */
export function useTaskVerificationDraft(latest: VerificationDraft) {
  const key = JSON.stringify(latest);
  const [stored, setStored] = useState({ key, baseline: latest, draft: latest });
  let editor = stored;
  if (stored.key !== key) {
    editor = { key, baseline: latest, draft: reconcile(stored.baseline, stored.draft, latest) };
    setStored(editor);
  }
  return {
    ...editor,
    edit: (draft: VerificationDraft) => setStored((current) => ({ ...current, draft })),
    saved: (draft: VerificationDraft, before: VerificationDraft) => setStored((current) => ({ ...current, baseline: reconcile(before, draft, current.baseline) })),
  };
}
