import { useEffect, useRef, useState, type InputHTMLAttributes } from 'react';
import { settingFields } from './settingsFields';

export type NumericDraft = number | string;
export function NumericSettingInput({ fieldKey, value, onChange, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'> & { fieldKey: string; value: NumericDraft; onChange: (value: NumericDraft) => void }) {
  const field = settingFields[fieldKey];
  const integer = field?.kind === 'int';
  const input = useRef<HTMLInputElement>(null);
  const [buffer, setBuffer] = useState({ value, text: String(value) });
  if (buffer.value !== value) setBuffer({ value, text: String(value) });
  useEffect(() => {
    input.current?.setCustomValidity(integer && value !== '' && !Number.isSafeInteger(Number(value)) ? 'Enter a whole number within the supported integer range.' : '');
  }, [integer, value]);
  return <input {...props} ref={input} type="number" required min={field?.min} max={field?.max} step={field?.step || (integer ? '1' : 'any')} value={buffer.text} onChange={(event) => {
    const raw = event.target.value; const numeric = event.target.valueAsNumber;
    const next = raw && Number.isFinite(numeric) && (!integer || Number.isSafeInteger(numeric)) ? numeric : raw;
    setBuffer({ value: next, text: raw }); onChange(next);
  }} />;
}
