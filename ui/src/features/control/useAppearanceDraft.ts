import { useLayoutEffect, useState } from 'react';
import { applyAppearance, readAppearance, saveAppearance } from '../../app/preferences';
import { settingsEqual } from './settingsModel';

/** The Settings owner previews locally and commits only after coordinated saves. */
export function useAppearanceDraft() {
  const [baseline, setBaseline] = useState(readAppearance);
  const [value, setValue] = useState(baseline);
  const dirty = !settingsEqual(baseline, value);
  useLayoutEffect(() => { applyAppearance(value); }, [value]);
  useLayoutEffect(() => () => { applyAppearance(readAppearance()); }, []);
  const commit = () => {
    if (!dirty) return;
    if (!saveAppearance(value)) throw new Error('Appearance could not be saved on this device');
    setBaseline(value);
  };
  return { value, setValue, dirty, commit };
}
