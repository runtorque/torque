import { describe, expect, it } from 'vitest';
import { providerChoices } from './providerChoices';
const providers = [{ name: 'first', display_name: 'First CLI', reasoning_efforts: ['low', 'high'], models: [{ id: 'one', display_name: 'Model one', supported_reasoning_efforts: [{ value: 'max', description: 'Maximum' }, 'max'] }] }, { name: 'second', models: [{ id: 'two' }] }];
describe('provider discovery choices', () => {
  it('uses daemon model-specific efforts, supports grouped fields, and never invents inheritance', () => {
    expect(providerChoices(providers, 'provider', {})).toEqual([{ value: 'first', label: 'First CLI' }, { value: 'second', label: 'second' }]);
    expect(providerChoices(providers, 'engineer_model', { engineer_provider: 'second' })).toEqual([{ value: 'two', label: 'two' }]);
    expect(providerChoices(providers, 'reasoning_effort', { provider: 'first', model: 'one' })).toEqual([{ value: 'max', label: 'Maximum' }]);
    expect(providerChoices(providers, 'reasoning_effort', { provider: 'first', model: 'custom' })).toEqual([{ value: 'low', label: 'low' }, { value: 'high', label: 'high' }]);
    expect(providerChoices(providers, 'model', { provider: '' })).toEqual([]);
    expect(providerChoices([], 'model', { provider: 'first' })).toEqual([]);
  });
});
