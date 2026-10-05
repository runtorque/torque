import { useEffect, useRef, useState } from 'react';
import { useAppStore } from './hooks';
import { installWorkspaceNavigation } from './workspaceNavigationPersistence';
export function useWorkspaceNavigation(detached: boolean) {
  const store = useAppStore(); const [error, setError] = useState('');
  const retry = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (detached) return;
    const persistence = installWorkspaceNavigation(store, setError); retry.current = persistence.retry;
    return () => { retry.current = null; persistence.dispose(); };
  }, [store, detached]);
  return { error, retry: () => retry.current?.() };
}
