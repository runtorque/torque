import { expect, it } from 'vitest';
import { terminalScrollback } from './terminalPreferences';
it.each([[undefined, 2000], [null, 2000], ['', 2000], ['bad', 2000], [Infinity, 2000], [NaN, 2000], [99, 2000], [100001, 2000], [100, 100], ['3500', 3500], [300.9, 300], [100000, 100000]])('normalizes saved scrollback %s to %s using Classic bounds', (value, expected) => {
  expect(terminalScrollback(value)).toBe(expected);
});
