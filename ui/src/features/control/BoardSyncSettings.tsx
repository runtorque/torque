import { useEffect, useState } from 'react';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { record, text } from './agentClassesModel';
import { settingsEqual } from './settingsModel';
import styles from './ControlCenter.module.css';

type Mode = 'projects' | 'test' | 'repository' | 'project';
type Request = { mode: Mode; settings: UnknownRecord; source: UnknownRecord; key: string };
type Result = { key: string; pending?: boolean; error?: string; message?: string; frame?: UnknownRecord };
function draft(settings: UnknownRecord): UnknownRecord {
  return { board_sync_provider: settings.board_sync_provider, board_sync_enabled: settings.board_sync_enabled, board_sync_github: record(settings.board_sync_github) };
}
function identity(group: string, settings: UnknownRecord) { return JSON.stringify([group, draft(settings)]); }
function projectValue(project: UnknownRecord) { return JSON.stringify([text(project.owner), project.number, text(project.id)]); }
function failure(frame: UnknownRecord) {
  const message = text(frame.error, text(frame.message, 'GitHub configuration check failed.'));
  return frame.phase === 'project_scope' && !message.includes('gh auth refresh -s project') ? `${message} Run: gh auth refresh -s project` : message;
}
export function BoardSyncSettings({ group, settings, onChange, disabled = false }: { disabled?: boolean; group: string; settings: UnknownRecord; onChange: (github: UnknownRecord) => void }) {
  const [request, setRequest] = useState<Request | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [projects, setProjects] = useState<{ group: string; rows: UnknownRecord[] } | null>(null);
  const key = identity(group, settings); const github = record(settings.board_sync_github);
  // The request carries its exact draft. Changed configuration cancels it, so a
  // late response cannot overwrite typing, a reset, or a different group.
  useEffect(() => {
    if (disabled || !request || request.key !== key || request.source.board_sync_provider !== 'github') return;
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 30000);
    let disposed = false;
    const run = async () => {
      try {
        const config = record(request.source.board_sync_github);
        const command = request.mode === 'projects' ? 'board_sync_list_projects' : 'board_sync_preflight';
        const frame = await readCommand({ cmd: command, group, provider: 'github', settings: request.settings, ...(request.mode === 'projects' ? { owner: text(config.github_project_owner).trim() } : {}) }, controller.signal);
        if (disposed) return;
        if (frame.type !== command || frame.group !== group || frame.provider !== 'github' || frame.ok !== true) throw new Error(failure(frame));
        if (request.mode === 'projects') {
          if (!Array.isArray(frame.projects)) throw new Error('GitHub project response was invalid.');
          const rows = frame.projects.map(record).filter((project) => text(project.owner) && Number.isSafeInteger(project.number) && Number(project.number) > 0);
          setProjects({ group, rows });
          const errors = Array.isArray(frame.errors) ? frame.errors.map(record).map(failure) : [];
          setResult({ key, message: rows.length ? `Loaded ${rows.length} accessible GitHub project${rows.length === 1 ? '' : 's'}.` : 'No accessible projects. Check GitHub authentication or change the project owner and retry.', ...(errors.length ? { error: `Some owners could not be checked: ${errors.join(' ')}` } : {}) });
          return;
        }
        const next = { ...config };
        if (request.mode === 'repository' && text(frame.repo, text(frame.repository))) next.github_repo = text(frame.repo, text(frame.repository));
        if (text(frame.project_owner)) next.github_project_owner = frame.project_owner;
        if (Number.isSafeInteger(frame.project_number) && Number(frame.project_number) > 0) next.github_project_number = frame.project_number;
        if (text(frame.project_id)) next.github_project_id = frame.project_id;
        const suggestion = Object.fromEntries(Object.entries(record(frame.lane_status_map_suggestion)).filter(([lane, status]) => lane.trim() && typeof status === 'string' && status.trim()));
        const applyMap = !Object.keys(record(config.github_lane_status_map)).length && Object.keys(suggestion).length > 0;
        if (applyMap) next.github_lane_status_map = suggestion;
        setResult({ key: identity(group, { ...request.source, board_sync_github: next }), message: `GitHub connection OK${text(frame.repo) ? `: ${text(frame.repo)}` : ''}.${applyMap ? ` Filled empty lane mapping (${text(frame.lane_status_map_strategy, 'matching names')}).` : ''}`, frame });
        if (!settingsEqual(config, next)) onChange(next);
      } catch (cause: unknown) {
        if (!disposed) setResult({ key, error: controller.signal.aborted ? 'GitHub check timed out. Retry when the connection is available.' : cause instanceof Error ? cause.message : 'GitHub check failed.' });
      } finally { clearTimeout(timer); setRequest((current) => current === request ? null : current); }
    };
    void run();
    return () => { disposed = true; clearTimeout(timer); controller.abort(); setRequest((current) => current === request ? null : current); };
  }, [request, key, group, onChange, disabled]);
  const run = (mode: Mode, next = settings) => {
    const config = record(next.board_sync_github); const number = config.github_project_number;
    if (number !== undefined && (!Number.isSafeInteger(number) || Number(number) < 0)) { setResult({ key, error: 'Enter a non-negative whole GitHub project number before checking.' }); return; }
    const nextKey = identity(group, next);
    const source = draft(next);
    const query = mode === 'repository' ? { ...source, board_sync_github: { ...config, github_repo: '' } } : source;
    setResult({ key: nextKey, pending: true }); setRequest({ mode, source, settings: query, key: nextKey });
  };
  if (settings.board_sync_provider !== 'github') return null;
  const current = result?.key === key ? result : null;
  const pending = current?.pending === true && request !== null && !disabled;
  const rows = projects?.group === group ? projects.rows : [];
  const selected = { owner: github.github_project_owner, number: github.github_project_number, id: github.github_project_id };
  const selectedValue = projectValue(selected); const listed = rows.some((row) => projectValue(row) === selectedValue);
  const unmatched = Array.isArray(current?.frame?.lane_status_map_unmatched_lanes) ? current.frame.lane_status_map_unmatched_lanes.filter((item): item is string => typeof item === 'string') : [];
  const statuses = Object.keys(record(current?.frame?.status_options));
  return <section aria-label="GitHub sync connection" className={styles.syncSettings}>
    <h3>GitHub sync connection</h3><p>Check the current draft. Save changes to apply this configuration. Repository detection uses the daemon’s working directory.</p>
    <div><Button isDisabled={disabled || pending} onPress={() => run('projects')}>Load GitHub projects</Button><Button isDisabled={disabled || pending} onPress={() => run('test')}>Test GitHub connection</Button><Button isDisabled={disabled || pending} onPress={() => run('repository')}>Use current repository</Button></div>
    <label className={styles.field}><span>Accessible GitHub project</span><select disabled={disabled} value={listed || text(selected.owner) || Number(selected.number) > 0 ? selectedValue : ''} onChange={(event) => {
      const project = rows.find((row) => projectValue(row) === event.target.value); if (!project) return;
      const next = { ...github, github_project_owner: project.owner, github_project_number: project.number, github_project_id: text(project.id) };
      onChange(next); run('project', { ...settings, board_sync_github: next });
    }}><option value="">Select a project</option>{!listed && (text(selected.owner) || Number(selected.number) > 0) ? <option value={selectedValue}>Configured manually: {text(selected.owner)} #{typeof selected.number === 'number' ? selected.number : text(selected.number)}</option> : null}{rows.map((project) => <option key={projectValue(project)} value={projectValue(project)}>{text(project.owner)} · #{Number(project.number)} — {text(project.name, text(project.title, 'Untitled project'))}</option>)}</select></label>
    {pending ? <p role="status">Checking GitHub…</p> : null}{current?.pending && !pending ? <p role="status">Check canceled. Run it again to inspect this draft.</p> : null}{current?.message ? <p role="status">{current.message}</p> : null}{current?.error ? <p role="alert">{current.error}</p> : null}
    {result && !current ? <p>Configuration changed. Run the check again for this draft.</p> : null}
    {statuses.length ? <p>Project status options: {statuses.join(', ')}</p> : null}{unmatched.length ? <p>Map these unmatched lanes manually: {unmatched.join(', ')}</p> : null}
  </section>;
}
