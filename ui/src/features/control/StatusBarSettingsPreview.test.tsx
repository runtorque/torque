import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StatusBarSettingsPreview } from './StatusBarSettingsPreview';

describe('status-bar settings preview', () => {
  it('shows sample values for all supported indicators without actionable controls', () => {
    render(<StatusBarSettingsPreview visibility={{ daemon_status: true, deploy: true, health: true, workload: true, tasks: true, attention: true, claude_usage: true, codex_usage: true, unknown: true }} />);
    const preview = screen.getByRole('group', { name: 'Status bar preview' });
    expect(within(preview).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['● Daemon connected', 'Deploy +2', 'Health good', 'Agents 3 run', 'Tasks 4 active', 'Attention 1', 'Claude 42%', 'Codex 20%']);
    expect(preview).toHaveTextContent('Sample values'); expect(within(preview).queryByRole('button')).not.toBeInTheDocument();
  });
  it('tracks the current draft including explicit empty selections and preserves retained segments', () => {
    const { rerender } = render(<StatusBarSettingsPreview visibility={{ deploy: true, tasks: true, health: false }} />);
    const task = screen.getByText('Tasks 4 active');
    rerender(<StatusBarSettingsPreview visibility={{ deploy: false, tasks: true, health: true }} />);
    expect(screen.queryByText('Deploy +2')).not.toBeInTheDocument(); expect(screen.getByText('Tasks 4 active')).toBe(task); expect(screen.getByText('Health good')).toBeVisible();
    rerender(<StatusBarSettingsPreview visibility={{ deploy: false, tasks: false, health: false }} />);
    expect(screen.queryByRole('list')).not.toBeInTheDocument(); expect(screen.getByText('No optional items selected')).toBeVisible();
  });
});
