import { createContext, useContext, useLayoutEffect, useState } from 'react';

type Protection = { purpose?: 'settings' | 'creation'; dirty: boolean; saving: boolean; group: string; discard: () => void; restoreFocus: () => void };
export type SettingsRegistration = Protection & { token: symbol };
interface SettingsNavigation {
  request: (action: () => void) => void;
  retainedGroup: string | null;
  update: (value: SettingsRegistration) => void;
  clear: (token: symbol) => void;
}
export const SettingsNavigationContext = createContext<SettingsNavigation>({ request: (action) => action(), retainedGroup: null, update: () => {}, clear: () => {} });
export const useSettingsNavigation = () => useContext(SettingsNavigationContext);

export function useSettingsProtection(value: Protection) {
  const { update, clear } = useSettingsNavigation();
  const [token] = useState(() => Symbol('settings owner'));
  const { dirty, saving, group, discard, restoreFocus, purpose = 'settings' } = value;
  useLayoutEffect(() => { update({ token, dirty, saving, group, discard, restoreFocus, purpose }); }, [update, token, dirty, saving, group, discard, restoreFocus, purpose]);
  useLayoutEffect(() => () => clear(token), [clear, token]);
}
