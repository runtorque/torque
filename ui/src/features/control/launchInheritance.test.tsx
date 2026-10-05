import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { UnknownRecord } from '../../protocol';
import { StructuredSettings } from './StructuredSettings';
import { launchInheritance } from './launchInheritance';
import { providerChoices } from './providerChoices';

const providers = [
  { name: 'one', display_name: 'First CLI', command: 'first', models: [{ id: 'model-a', display_name: 'Model A', is_default: true, default_reasoning_effort: 'high', reasoning_efforts: ['low', 'high'] }] },
  { name: 'two', display_name: 'Second CLI', command: 'second', models: [{ id: 'model-b', display_name: 'Model B', is_default: true, default_reasoning_effort: 'max', reasoning_efforts: ['max'] }] },
];
function options(label: string) {
  const input = screen.getByLabelText(label); const list = document.getElementById(input.getAttribute('list') ?? '');
  return list ? Array.from(list.querySelectorAll('option')).map((option) => option.value) : [];
}
describe('launch inheritance in settings', () => {
  it.each(['worker', 'engineer', 'architect'])('resolves %s choices from unsaved shared defaults while keeping overrides sparse', (kind) => {
    const role: UnknownRecord = { [`${kind}_provider`]: '', [`${kind}_model`]: '', [`${kind}_reasoning_effort`]: '', [`${kind}_boot_command`]: '' };
    function Form() {
      const [group, setGroup] = useState<UnknownRecord>({ agent_provider: '', agent_model: '' }); const [value, setValue] = useState(role);
      return <><StructuredSettings value={group} onChange={setGroup} providers={providers} /><StructuredSettings value={value} onChange={setValue} providers={providers} launchContext={{ group, runtime: { default_command: 'first --local' } }} /><output aria-label="Role draft">{JSON.stringify(value)}</output></>;
    }
    render(<Form />); const title = kind[0]!.toUpperCase() + kind.slice(1);
    expect(options(`${title} model`)).toEqual(['model-a']);
    expect(options(`${title} reasoning effort`)).toEqual(['low', 'high']);
    expect(screen.getByLabelText(`${title} provider`)).toHaveAttribute('placeholder', 'Inherit · First CLI');
    expect(screen.getByLabelText(`${title} model`)).toHaveAttribute('placeholder', 'Inherit · Model A');
    fireEvent.change(screen.getByLabelText('Agent provider'), { target: { value: 'two' } });
    expect(options(`${title} model`)).toEqual(['model-b']); expect(options(`${title} reasoning effort`)).toEqual(['max']);
    expect(screen.getByLabelText(`${title} boot command`)).toHaveAttribute('placeholder', 'Inherit · second');
    expect(JSON.parse(screen.getByLabelText('Role draft').textContent ?? '{}')).toEqual(role);
    fireEvent.change(screen.getByLabelText(`${title} model`), { target: { value: 'custom-model' } });
    fireEvent.change(screen.getByLabelText('Agent provider'), { target: { value: 'one' } });
    expect(screen.getByLabelText(`${title} model`)).toHaveValue('custom-model');
  });
  it('shows inherited command, directory, shell and environment without changing the draft', () => {
    const change = () => { throw new Error('Preview must not edit'); };
    render(<StructuredSettings providers={providers} value={{ engineer_boot_command: '', engineer_directory: '', engineer_shell: '', agent_env_file: '' }} onChange={change} launchContext={{ group: { agent_provider: 'one', agent_boot_command: 'custom --run', agent_directory: '/project/agents', default_directory: '/project', agent_shell: 'fish', env_file: '/project/.env' }, runtime: {} }} />);
    expect(screen.getByLabelText('Engineer boot command')).toHaveAttribute('placeholder', 'Inherit · custom --run');
    expect(screen.getByLabelText('Engineer directory')).toHaveAttribute('placeholder', 'Inherit · /project/agents');
    expect(within(screen.getByLabelText('Engineer shell')).getByRole('option', { name: 'Inherit · fish' })).toHaveValue('');
    expect(screen.getByLabelText('Agent env file')).toHaveAttribute('placeholder', 'Inherit · /project/.env');
  });
});

it('prefers explicit providers and models, respects unknown providers, and does not invent runtime metadata', () => {
  const context = { group: { agent_provider: 'one', agent_model: 'model-a', agent_reasoning_effort: 'low' }, runtime: { default_command: 'first' } };
  const values = { engineer_provider: 'two', engineer_model: 'model-b', engineer_reasoning_effort: '' };
  const resolved = launchInheritance('engineer_reasoning_effort', values, providers, context);
  expect(providerChoices(providers, 'engineer_reasoning_effort', resolved.choices)).toEqual([{ value: 'max', label: 'max' }]);
  expect(resolved.placeholder).toBe('Inherit · low');
  expect(values).toEqual({ engineer_provider: 'two', engineer_model: 'model-b', engineer_reasoning_effort: '' });
  const unknown = launchInheritance('engineer_model', { engineer_provider: 'custom' }, providers, context);
  expect(providerChoices(providers, 'engineer_model', unknown.choices)).toEqual([]);
  const unavailable = launchInheritance('agent_model', { agent_provider: '' }, providers, { group: {}, runtime: { default_command: 'unknown --custom' } });
  expect(providerChoices(providers, 'agent_model', unavailable.choices)).toEqual([]);
  expect(unavailable.placeholder).toBe('Inherit · provider default');
});

it('refreshes inherited previews without replacing a focused custom model draft or its selection', () => {
  const values = { architect_provider: '', architect_model: 'my-custom-model' };
  const view = render(<StructuredSettings value={values} onChange={() => undefined} providers={providers} launchContext={{ group: { agent_provider: 'one' }, runtime: {} }} />);
  const input = screen.getByLabelText<HTMLInputElement>('Architect model'); input.focus(); input.setSelectionRange(3, 8);
  view.rerender(<StructuredSettings value={values} onChange={() => undefined} providers={providers} launchContext={{ group: { agent_provider: 'two' }, runtime: {} }} />);
  expect(screen.getByLabelText('Architect model')).toBe(input); expect(input).toHaveValue('my-custom-model'); expect(input).toHaveFocus();
  expect([input.selectionStart, input.selectionEnd]).toEqual([3, 8]); expect(options('Architect model')).toEqual(['model-b']);
});
