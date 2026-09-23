import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { UnknownRecord } from '../../protocol';
import { toAgentViewModel } from './model';
import { WorktreeDiff } from './WorktreeDiff';
import { WorktreeInspector } from './WorktreeInspector';
function file(path: string, lengths: number[]): UnknownRecord {
  let line = 0;
  return { path, status: 'modified', insertions: lengths.reduce((sum, count) => sum + count, 0), hunks: lengths.map((count, index) => ({ header: `@@ hunk ${index} @@`, lines: Array.from({ length: count }, () => ({ type: 'add', text: `${path} line ${++line}` })) })) };
}
it.each([
  [1, 800, 0], [1, 801, 1], [12, 2, 0], [13, 2, 12], [2, 750, 0], [2, 751, 2],
])('matches Classic auto-collapse thresholds (%i files of %i lines)', (files, lines, collapsed) => {
  render(<WorktreeDiff files={Array.from({ length: files }, (_, index) => file(`${index}.txt`, [lines]))} />);
  expect(screen.getByText(`${collapsed} of ${files} collapsed`)).toBeInTheDocument();
});
it('waits for data, previews one small file, and mounts large files only in bounded line chunks', () => {
  const view = render(<WorktreeDiff files={[]} />);
  const files = [file('large.txt', [300, 750, 1000]), file('small.txt', [3]), { path: 'binary.png', binary: true }];
  view.rerender(<WorktreeDiff files={files} />);
  const region = screen.getByRole('region', { name: 'Worktree diff files' });
  expect(region.querySelectorAll('pre span')).toHaveLength(3); expect(screen.getByText('2 of 3 collapsed')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /large.txt/ }));
  expect(region.querySelectorAll('pre span')).toHaveLength(403); expect(screen.getByText('@@ hunk 1 @@')).toBeInTheDocument(); expect(screen.queryByText('@@ hunk 2 @@')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show 400 more lines' })); expect(region.querySelectorAll('pre span')).toHaveLength(803);
  fireEvent.click(screen.getByRole('button', { name: 'Collapse all' })); expect(region.querySelectorAll('pre span')).toHaveLength(0); expect(screen.getByRole('button', { name: 'Collapse all' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Expand all' })); expect(region.querySelectorAll('pre span')).toHaveLength(803); expect(screen.getByText('Binary file changed.')).toBeInTheDocument();
  for (let count = 0; count < 3; count++) fireEvent.click(screen.getByRole('button', { name: 'Show 400 more lines' }));
  fireEvent.click(screen.getByRole('button', { name: 'Show 50 more lines' })); expect(region.querySelectorAll('pre span')).toHaveLength(2053); expect(screen.queryByRole('button', { name: /more lines/ })).not.toBeInTheDocument();
});
it('keeps path-keyed disclosure, loaded lines and existing nodes through refreshed and reordered diffs', () => {
  const files = [file('first.txt', [650]), file('second.txt', [2])]; const view = render(<WorktreeDiff files={files} />);
  fireEvent.click(screen.getByRole('button', { name: 'Show 250 more lines' }));
  const header = screen.getByRole('button', { name: /first.txt/ }); header.focus();
  fireEvent.click(screen.getByRole('button', { name: /second.txt/ }));
  view.rerender(<WorktreeDiff files={[file('second.txt', [4]), file('first.txt', [655]), file('new.txt', [1])]} />);
  expect(screen.getByRole('button', { name: /first.txt/ })).toBe(header); expect(header).toHaveFocus(); expect(screen.getByRole('button', { name: /second.txt/ })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByRole('region').querySelectorAll('pre span')).toHaveLength(656);
});
it('retains diff nodes and disclosure across inspector tabs and explicit refresh', () => {
  const agent = toAgentViewModel('qa', { id: 'qa', name: 'QA', worktree_path: '/tmp/qa', kind: 'worker' }); const send = vi.fn(() => true);
  const props = { agent, sendCommand: send, onUnavailable: vi.fn(), onClose: vi.fn() };
  const responses = { 'worktree_diff_full:qa': { files: [file('first.txt', [650])] }, 'worktree_check_merge:qa': { clean: true }, 'worktree_history:qa': { commits: [] } };
  const view = render(<WorktreeInspector {...props} responses={responses} />);
  fireEvent.click(screen.getByRole('button', { name: 'Show 250 more lines' })); const region = screen.getByRole('region');
  const nav = screen.getByRole('navigation', { name: 'Worktree views' }); fireEvent.click(within(nav).getByRole('button', { name: /History/ })); expect(region).not.toBeVisible();
  fireEvent.click(within(nav).getByRole('button', { name: 'Changes' })); expect(screen.getByRole('region')).toBe(region); expect(region.querySelectorAll('pre span')).toHaveLength(650);
  fireEvent.click(within(nav).getByRole('button', { name: 'Refresh' })); expect(send.mock.calls).toHaveLength(6);
  view.rerender(<WorktreeInspector {...props} responses={{ ...responses, 'worktree_diff_full:qa': { files: [file('first.txt', [655])] } }} />);
  expect(region.querySelectorAll('pre span')).toHaveLength(655);
});
