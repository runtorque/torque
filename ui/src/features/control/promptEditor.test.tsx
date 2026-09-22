import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import { PromptEditor } from './PromptEditor';
import { promptTokens, type PromptToken } from './promptSyntax';
function flatten(tokens: PromptToken[]): string { return tokens.map((token) => token.children ? flatten(token.children) : token.text).join(''); }
it('preserves complete, partial, quoted, multiline and Unicode prompt text exactly', () => {
  const fixtures = ['', 'plain text\n\twith tabs\n', '{{ TASK }}', '{{ torque.task.title | default("example") }}', '{% if TASK %}\n{{ TASK }}\n{% endif %}', '{# comment "quoted #}\n', '{{ "}}" | default(\'value\') }} tail', '{{ "escaped \\" quote" }}', '{{ unfinished', '{{ "unfinished quote\\', 'Olá 👋\n{{ TASK }}\n', '<img onerror="alert(1)"> {{ TASK }}', '{{- TASK -}}'];
  for (const value of fixtures) { const tokens = promptTokens(value); expect(tokens.map((token) => token.text).join('')).toBe(value); expect(flatten(tokens)).toBe(value); }
  expect(promptTokens('{{ "}}" | default(\'value\') }} tail')).toHaveLength(2);
  expect(promptTokens('{{ torque.task.title | default("example") }}')[0]?.children?.map((token) => token.kind).filter(Boolean)).toEqual(['filter', 'paren', 'string', 'paren']);
});
it('uses one labelled native textarea and a hidden inert highlighting backdrop', () => {
  const value = '<img src=x onerror=alert(1)> {{ TASK | default("<b>safe</b>") }}';
  const { container } = render(<label>Prompt<PromptEditor value={value} onChange={() => {}} /></label>);
  expect(screen.getAllByRole('textbox')).toHaveLength(1); expect(screen.getByRole('textbox', { name: 'Prompt' })).toHaveValue(value);
  const pre = container.querySelector('pre')!; expect(pre).toHaveAttribute('aria-hidden', 'true'); expect(pre.textContent).toBe(`${value}\u200b`); expect(pre.querySelector('[data-syntax="filter"]')).toHaveTextContent('| default'); expect(pre.querySelector('[data-syntax="string"]')).toHaveTextContent('"<b>safe</b>"'); expect(container.querySelector('img, b, script')).toBeNull();
});
it('keeps input identity, focus, caret, scroll and composition text during rerender', () => {
  const onChange = vi.fn(); const view = render(<label>Prompt<PromptEditor value={'line one\n{{ TASK }}'} onChange={onChange} /></label>);
  const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Prompt' }); const pre = view.container.querySelector('pre')!;
  input.focus(); input.setSelectionRange(2, 6); input.scrollTop = 40; input.scrollLeft = 20; fireEvent.scroll(input); expect(pre.scrollTop).toBe(40); expect(pre.scrollLeft).toBe(20);
  view.rerender(<label>Prompt<PromptEditor value={'line one\n{{ TASK }}'} onChange={onChange} /></label>); expect(screen.getByRole('textbox')).toBe(input); expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([2, 6]); expect(input.scrollTop).toBe(40);
  fireEvent.compositionStart(input); fireEvent.change(input, { target: { value: 'Olá 日本語 {{ TASK }}\n' } }); fireEvent.compositionEnd(input); expect(onChange).toHaveBeenLastCalledWith('Olá 日本語 {{ TASK }}\n');
});
it('updates highlighting without changing the controlled draft or its trailing newline', () => {
  function Editor() { const [value, setValue] = useState(''); return <label>Prompt<PromptEditor value={value} onChange={setValue} /></label>; }
  const { container } = render(<Editor />); const input = screen.getByRole('textbox');
  const value = '{% if TASK %}\n{{ TASK | default("all") }}\n{# note #}\n{% endif %}\n'; fireEvent.change(input, { target: { value } }); expect(input).toHaveValue(value); expect(container.querySelector('pre')?.textContent).toBe(`${value}\u200b`); expect(container.querySelectorAll('[data-syntax="statement"]')).toHaveLength(2); expect(container.querySelector('[data-syntax="comment"]')).toHaveTextContent('{# note #}');
});
