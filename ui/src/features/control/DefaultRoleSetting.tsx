import type { UnknownRecord } from '../../protocol';

export function DefaultRoleSetting({ value, templates, label, descriptionId, onChange }: { value: string; templates: unknown[]; label: string; descriptionId: string | undefined; onChange: (value: string) => void }) {
  const rows = templates.filter((item): item is UnknownRecord => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .filter((item) => typeof item.name === 'string' && item.name.trim() && item.shadowed !== true);
  const user = (item: UnknownRecord) => item.global === true || item.scope === 'user';
  const byName = new Map<string, UnknownRecord>();
  // Project precedence remains correct even if an older catalog omits shadowed.
  for (const item of [...rows.filter((row) => !user(row)), ...rows.filter(user)]) {
    if (!byName.has(item.name as string)) byName.set(item.name as string, item);
  }
  return <select aria-label={label} aria-describedby={descriptionId} value={value} onChange={(event) => onChange(event.target.value)}>
    <option value="">None</option>
    {value && !byName.has(value) ? <option value={value}>{value} (not in current catalog)</option> : null}
    {(['Project', 'User'] as const).map((scope) => {
      const options = [...byName.entries()].filter(([, item]) => user(item) === (scope === 'User'));
      return options.length ? <optgroup key={scope} label={scope}>{options.map(([name, item]) => <option key={name} value={name}>{typeof item.display_name === 'string' && item.display_name ? item.display_name : name}</option>)}</optgroup> : null;
    })}
  </select>;
}
