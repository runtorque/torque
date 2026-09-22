import type { ComposerDraft } from './composerState';
interface ComposerStore { getState(): { composer: { drafts: Record<string, ComposerDraft> } }; subscribe(listener: () => void): () => void }
export function installComposerPreviewCleanup(store: ComposerStore): void {
  let previousDrafts = store.getState().composer.drafts; let previous = new Set<string>();
  store.subscribe(() => {
    const drafts = store.getState().composer.drafts; if (drafts === previousDrafts) return;
    previousDrafts = drafts; const current = new Set<string>();
    for (const draft of Object.values(drafts)) {
      for (const snapshot of [draft, ...draft.undo, draft.recall?.document, draft.composition, draft.completedUpload].filter((value) => Boolean(value))) {
        for (const attachment of snapshot?.attachments ?? []) if (attachment.previewUrl?.startsWith('blob:')) current.add(attachment.previewUrl);
      }
    }
    for (const url of previous) if (!current.has(url)) URL.revokeObjectURL?.(url);
    previous = current;
  });
}
