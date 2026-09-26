import type { UnknownRecord } from '../../protocol';
import { text, type Scope } from './model';
import styles from './BehaviorOverlayEditor.module.css';
export function BehaviorScopeGuidance({ scope }: { scope: Scope }) {
  return <p>{scope.kind === 'role'
    ? `This additive ${scope.target} role overlay applies group-wide and requires user diff approval. Approved guidance takes effect on ${scope.target === 'worker' ? 'the next worker dispatch' : 'the next launch or relaunch'}.`
    : 'This additive overlay applies only to the selected agent. Approved guidance takes effect on its next launch or relaunch.'}</p>;
}
export function BehaviorProposalSummary({ proposal }: { proposal: UnknownRecord }) {
  const count = typeof proposal.lint_warning_count === 'number' ? proposal.lint_warning_count : null;
  return <dl className={styles.provenance}>
    <div><dt>Change type</dt><dd>{proposal.proposal_type === 'rollback' ? 'Rollback' : proposal.proposal_type === 'set_text' ? 'Set text' : text(proposal.proposal_type) || 'Not reported'}</dd></div>
    <div><dt>Approval route</dt><dd>{text(proposal.approval_route) || 'Not reported'}</dd></div>
    <div><dt>Proposed size</dt><dd>{typeof proposal.proposed_text_bytes === 'number' ? `${proposal.proposed_text_bytes} bytes` : 'Not reported'}</dd></div>
    <div><dt>Proposed SHA-256</dt><dd>{text(proposal.proposed_text_sha256) || 'Not reported'}</dd></div>
    <div><dt>Advisory lint</dt><dd>{count === null ? 'Review for details' : `${count} warning${count === 1 ? '' : 's'}`}</dd></div>
  </dl>;
}
