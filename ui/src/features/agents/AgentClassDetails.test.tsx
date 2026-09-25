import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { UnknownRecord } from '../../protocol';
import { AgentClassDetails } from './AgentClassDetails';
import { classPermissions, classTime, classWarnings, selectedClassPreview } from './agentClassPresentation';

const connector = 'External connector exposure is not governed by Agent Classes; manage connector access separately.';
const warning = 'Review Agent Classes before launch.';
const current: UnknownRecord = { id: 'current', primary_identity_label: 'Current identity', secondary_base_kind_label: 'Worker-derived', version: '3', status: 'restricted', lifecycle: 'stable', warnings: ['Frozen warning', connector] };
const selected: UnknownRecord = { id: 'selected', primary_identity_label: 'Selected identity', display_name: 'Old label', purpose: 'Selected purpose', description: 'Old description', secondary_base_kind_metadata: { base_kind_label: 'Worker-derived' }, version: '4', base_kind: 'worker', lifecycle: 'draft', status: 'restricted', draft: { scratch_only: true }, warnings: [warning, ' review agent class before launch! ', connector, 'External connector credentials have expired.'], acl: { mode: 'deny', rules: [{ capability: 'self.read', scope: 'self' }] }, authoring_definition: { acl: { mode: 'deny', rules: [{ capability: 'task.read', scope: 'group' }] } }, effective_authority: { capabilities: { 'self.read': 'self' } }, apply_state: { mutates_running_sessions: false, applies_at: 'next_launch_or_relaunch' } };
const status: UnknownRecord = { effective_class: current, effective_class_id: 'current', effective_class_version: '3', assigned_class: selected, assigned_class_id: 'selected', assigned_by: 'Operator', assigned_at: 1_700_000_000, effective_applied_at: 1_699_000_000, next_launch_class_id: 'selected', next_launch_class_version: '4', status: 'full', pending_next_launch: true, warnings: ['Stale fallback warning'] };
function details(extra: Partial<Parameters<typeof AgentClassDetails>[0]> = {}) { return <AgentClassDetails status={status} agent={{}} kind="worker" classes={[selected]} selectedId="selected" {...extra} />; }
const preview = () => screen.getByRole('region', { name: 'Selected Agent Class preview' });

describe('readable Agent Class details', () => {
  it('shows selected identity, lifecycle, scoped grants and authored denials without interpreting grants as denials', () => {
    render(details()); const region = within(preview());
    expect(region.getByRole('heading', { name: 'Selected identity@4' })).toBeVisible(); expect(region.getByText('Selected purpose')).toBeVisible(); expect(region.queryByText('Old description')).not.toBeInTheDocument();
    expect(region.getByText('Worker-derived')).toBeVisible(); expect(region.getByText('restricted')).toBeVisible(); expect(region.getByText('draft')).toBeVisible(); expect(region.getByText('Scratch only')).toBeVisible(); expect(region.getByText('deny')).toBeVisible();
    const access = region.getByRole('region', { name: 'Selected class access' }); const allowed = within(access).getByText('Allowed actions (1)'); fireEvent.click(allowed);
    expect(within(allowed.closest('details')!).getByText('self.read')).toBeVisible(); expect(within(allowed.closest('details')!).getByText('Scope: self')).toBeVisible(); expect(within(allowed.closest('details')!).queryByText('task.read')).not.toBeInTheDocument();
    expect(within(access).getByText('task.read')).toBeVisible(); expect(within(access).getByText('Scope: group')).toBeVisible();
    expect(region.getByText('Does not change running sessions.')).toBeVisible(); expect(region.getByText('Access freezes on the next launch or relaunch.')).toBeVisible(); expect(preview().querySelector('pre')).toBeNull();
  });

  it.each(['worker', 'engineer', 'architect'])('previews the implicit %s default even while a different class is effective', (kind) => {
    const defaultClass = { id: `default-${kind}`, display_name: `Default ${kind}`, version: '1', base_kind: kind, status: 'full', lifecycle: 'stable', acl: { mode: 'deny', capabilities: { 'self.read': 'self' } }, authoring_definition: { acl: { mode: 'deny', rules: [] } } };
    render(details({ kind, selectedId: '', classes: [selected, defaultClass] }));
    expect(within(preview()).getByRole('heading', { name: `Default ${kind}@1` })).toBeVisible(); expect(within(preview()).getByText(`No explicit class selected. Default launch behavior is preserved; Torque freezes default-${kind} at launch.`)).toBeVisible(); expect(within(preview()).getByText('No explicit denials.')).toBeVisible();
    expect(within(preview()).queryByText('Selected purpose')).not.toBeInTheDocument(); expect(screen.getByText('Current identity')).toBeVisible();
  });

  it('deduplicates actionable warnings and keeps frozen warnings distinct through selection and catalog refresh', () => {
    const view = render(details()); const selectedWarnings = screen.getByRole('region', { name: 'Selected class warnings' }); const currentWarnings = screen.getByRole('region', { name: 'Current class warnings' });
    expect(within(selectedWarnings).getAllByRole('listitem')).toHaveLength(2); expect(within(selectedWarnings).getByText(warning)).toBeVisible(); expect(within(selectedWarnings).getByText('External connector credentials have expired.')).toBeVisible();
    expect(within(currentWarnings).getByText('Frozen warning')).toBeVisible(); expect(within(currentWarnings).queryByText(warning)).not.toBeInTheDocument(); expect(screen.queryByText(connector)).not.toBeInTheDocument(); expect(screen.queryByText('Stale fallback warning')).not.toBeInTheDocument();
    const summary = screen.getByText('Allowed actions (1)'); fireEvent.click(summary); summary.focus();
    view.rerender(details({ classes: [{ ...selected, version: '5', warnings: ['Fresh selected warning'] }] }));
    expect(screen.getByText('Allowed actions (1)')).toBe(summary); expect(summary).toHaveFocus(); expect(summary.closest('details')).toHaveAttribute('open'); expect(screen.getByText('Frozen warning')).toBeVisible(); expect(screen.getByText('Fresh selected warning')).toBeVisible(); expect(screen.queryByText(warning)).not.toBeInTheDocument();
    view.rerender(details({ selectedId: 'missing', classes: [] })); expect(screen.getByText('Frozen warning')).toBeVisible(); expect(screen.queryByRole('region', { name: 'Selected class warnings' })).not.toBeInTheDocument();
  });

  it('shows current status metadata and fresh assignment/freeze timestamps with explicit zero suppressing stale raw values', () => {
    const view = render(details()); const facts = screen.getByRole('region', { name: 'Current Agent Class status' });
    expect(within(facts).getByText('Worker-derived')).toBeVisible(); expect(within(facts).getByText('restricted')).toBeVisible(); expect(within(facts).getByText('Operator')).toBeVisible();
    expect(facts.querySelector('time[datetime="2023-11-14T22:13:20.000Z"]')).toBeVisible(); expect(facts.querySelectorAll('time')).toHaveLength(2);
    view.rerender(details({ status: { ...status, assigned_at: 1_710_000_000, effective_applied_at: 0 }, agent: { effective_agent_class_applied_at: 1_699_000_000 } }));
    expect(facts.querySelectorAll('time')).toHaveLength(1); expect(facts.querySelector('time')?.getAttribute('datetime')).toBe(new Date(1_710_000_000_000).toISOString()); expect(within(facts).queryByText('Effective frozen')).not.toBeInTheDocument();
  });

  it('handles empty allow lists, unknown denials, archived classes and nonstandard apply timing explicitly', () => {
    const view = render(details({ classes: [{ ...selected, metadata: { archived_at: 100 }, effective_authority: { capabilities: {} }, acl: { mode: 'allow', capabilities: {} }, authoring_definition: {}, apply_state: { mutates_running_sessions: true, relaunch_required_after_assignment: false, applies_at: 'manual_boundary' } }] }));
    expect(within(preview()).getByText('archived')).toBeVisible(); expect(screen.getByText('No actions granted.')).toBeVisible(); expect(screen.getByText('Everything not listed is denied by default.')).toBeVisible(); expect(screen.getByText('May affect running sessions immediately.')).toBeVisible(); expect(screen.getByText('manual boundary')).toBeVisible();
    view.rerender(details({ classes: [{ ...selected, authoring_definition: {} }] })); expect(screen.getByText('Authored denials are unavailable in this snapshot.')).toBeVisible();
  });

  it('never substitutes an unrelated class for a missing selection or default', () => {
    expect(selectedClassPreview([], '', 'worker', status)).toEqual({}); expect(selectedClassPreview([], 'missing', 'worker', status)).toEqual({}); expect(selectedClassPreview([], 'selected', 'worker', status)).toBe(selected);
    render(details({ selectedId: '', classes: [] })); expect(within(preview()).getByText('Class details are unavailable. Refresh the catalog to inspect this selection.')).toBeVisible(); expect(within(preview()).queryByText('Selected purpose')).not.toBeInTheDocument();
  });

  it('uses canonical grants and bounds malformed warnings and times', () => {
    expect(classPermissions(selected)).toMatchObject({ mode: 'deny', grants: [{ capability: 'self.read', scope: 'self' }], denials: [{ capability: 'task.read', scope: 'group' }] });
    expect(classWarnings([warning, 'Review Agent Class before launch!', null, {}, connector])).toEqual([warning]);
    for (const value of [0, -1, 'bad', Infinity, 1e50]) expect(classTime(value)).toBeNull(); expect(classTime(1_700_000_000)?.iso).toBe(classTime(1_700_000_000_000)?.iso);
  });
});
