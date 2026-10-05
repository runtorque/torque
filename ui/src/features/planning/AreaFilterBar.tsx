import { Button } from '../../design/primitives';
import { areaLifecycles, type AreaFilters } from './areaModel';
import styles from './PlanningWorkspace.module.css';
export function AreaFilterBar({ value, types, count, total, onChange }: {
  value: AreaFilters; types: string[]; count: number; total: number; onChange: (filters: AreaFilters) => void;
}) {
  const active = Boolean(value.search || value.lifecycle || value.type);
  return <div className={styles.areaFilters} role="group" aria-label="Area filters">
    <label>Search<input type="search" aria-label="Search areas" placeholder="Search areas" value={value.search} onChange={(event) => onChange({ ...value, search: event.target.value })} /></label>
    <label>Lifecycle<select aria-label="Filter areas by lifecycle" value={value.lifecycle} onChange={(event) => onChange({ ...value, lifecycle: event.target.value })}><option value="">All lifecycles</option>{areaLifecycles.map((lifecycle) => <option key={lifecycle} value={lifecycle}>{lifecycle.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())}</option>)}</select></label>
    <label>Type<select aria-label="Filter areas by type" value={value.type} onChange={(event) => onChange({ ...value, type: event.target.value })}><option value="">All types</option>{value.type && !types.includes(value.type) ? <option value={value.type}>{value.type} (no current Areas)</option> : null}{types.map((type) => <option key={type} value={type}>{type}</option>)}</select></label>
    <span aria-label="Matching areas">{count} / {total}</span>
    <Button tone="quiet" isDisabled={!active} onPress={() => onChange({ search: '', lifecycle: '', type: '' })}>Clear filters</Button>
  </div>;
}
