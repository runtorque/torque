import { useCallback, useRef, useState, type PropsWithChildren } from 'react';
import { Button, ModalDialog } from '../design/primitives';

import { SettingsNavigationContext as Context, type SettingsRegistration as Registration } from './settingsNavigation';
const empty = { dirty: false, saving: false, group: null as string | null, purpose: 'settings' as 'settings' | 'creation' };
/** Hold the entire navigation action until the settings owner can leave. */
export function SettingsNavigationProvider({ children }: PropsWithChildren) {
  const owner = useRef<Registration | null>(null);
  const nextAction = useRef<(() => void) | null>(null);
  const [state, setState] = useState(empty);
  const [open, setOpen] = useState(false);
  const update = useCallback((value: Registration) => {
    owner.current = value;
    setState((previous) => {
      const group = value.dirty || value.saving ? value.group : previous.group;
      const purpose = value.purpose ?? 'settings';
      return previous.dirty === value.dirty && previous.saving === value.saving && previous.group === group && previous.purpose === purpose ? previous : { dirty: value.dirty, saving: value.saving, group, purpose };
    });
  }, []);
  const clear = useCallback((token: symbol) => {
    if (owner.current?.token !== token) return;
    if (owner.current.purpose === 'creation') { nextAction.current = null; setOpen(false); }
    owner.current = null; setState(empty);
  }, []);
  const request = useCallback((action: () => void) => {
    if (nextAction.current) return;
    if (owner.current?.dirty || owner.current?.saving) { nextAction.current = action; setOpen(true); }
    else { setState(empty); action(); }
  }, []);
  const cancel = () => {
    nextAction.current = null; setOpen(false);
    const restore = owner.current?.restoreFocus;
    if (restore) requestAnimationFrame(restore);
  };
  const proceed = () => {
    if (owner.current?.saving) return;
    const action = nextAction.current; nextAction.current = null;
    if (owner.current?.dirty) { const discard = owner.current.discard; owner.current = { ...owner.current, dirty: false }; discard(); }
    setState(empty); setOpen(false); action?.();
  };
  return <Context.Provider value={{ request, retainedGroup: state.group, update, clear }}>
    {children}
    <ModalDialog title={state.purpose === 'creation' ? state.saving ? 'Creation needs recovery' : 'Leave creation?' : state.saving ? 'Settings save in progress' : state.dirty ? 'Discard settings changes?' : 'Leave Settings?'} size="small" isOpen={open} onOpenChange={(value) => { if (!value) cancel(); }}>
      <p>{state.purpose === 'creation' ? state.saving ? 'Return to creation to recover or inspect the reviewed launch before leaving. Its request and draft remain active.' : 'Creation is no longer pending. Continue to your requested destination?' : state.saving ? 'Wait for the current save result before leaving Settings. Your draft and save remain active.' : state.dirty ? 'Discard the unsaved settings edits before leaving? Changes already saved will remain applied.' : 'No unsaved settings changes remain. Continue to your requested destination?'}</p>
      <Button autoFocus tone="quiet" onPress={cancel}>{state.purpose === 'creation' ? 'Return to creation' : 'Keep editing'}</Button>
      {!state.saving ? <Button tone={state.dirty ? 'danger' : 'primary'} onPress={proceed}>{state.dirty ? 'Discard changes' : 'Continue navigation'}</Button> : null}
    </ModalDialog>
  </Context.Provider>;
}
