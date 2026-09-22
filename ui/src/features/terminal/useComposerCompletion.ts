import { useId, useState, type KeyboardEvent } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectComposerCommands, selectTasksState, selectWorkspaceState } from '../../app/store';
import type { AgentViewModel } from '../agents/model';
import { composerCompletion, insertCompletion, type CompletionItem } from './completionModel';
export function useComposerCompletion(cell: AgentViewModel, target: AgentViewModel | null, value: string, selection: [number, number], enabled: boolean, onInsert: (text: string, selection: [number, number]) => void) {
  const catalog = useAppSelector(selectComposerCommands); const tasks = useAppSelector(selectTasksState); const workspace = useAppSelector(selectWorkspaceState);
  const group = cell.group || target?.group || (typeof workspace.activeGroup === 'string' ? workspace.activeGroup : '');
  const [focused, setFocused] = useState(false); const [composing, setComposing] = useState(false);
  const [dismissed, setDismissed] = useState(''); const [choice, setChoice] = useState({ key: '', id: '' });
  const identity = (text: string, caret: [number, number]) => JSON.stringify([cell.id, target?.id, target?.provider, group, text, caret]);
  const key = identity(value, selection); const id = useId();
  const completion = enabled && focused && !composing && dismissed !== key ? composerCompletion(value, selection, { catalog, tasks: tasks.records, group, provider: target?.provider ?? '', hasTarget: Boolean(target) }) : null;
  const index = completion && choice.key === key ? completion.items.findIndex((item) => item.id === choice.id) : -1;
  const pick = (item: CompletionItem) => { if (!completion) return; const next = insertCompletion(value, completion, item); setDismissed(identity(next.text, next.selection)); onInsert(next.text, next.selection); };
  const handleKey = (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!completion || event.nativeEvent.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return false;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setDismissed(key); return true; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); const next = event.key === 'ArrowDown' ? (index + 1) % completion.items.length : (index <= 0 ? completion.items.length : index) - 1;
      setChoice({ key, id: completion.items[next]!.id }); return true;
    }
    if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); pick(completion.items[Math.max(0, index)]!); return true; }
    return false;
  };
  return { id, completion, index, pick, handleKey, setFocused, setComposing, choose: (item: CompletionItem) => setChoice({ key, id: item.id }) };
}
