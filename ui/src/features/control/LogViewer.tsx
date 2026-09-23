import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { useAppSelector } from '../../app/hooks';
import { Button } from '../../design/primitives';
import { hasHostCapability, type DesktopHost } from '../../host';
import { matchesLog, type LogLine, type LogPage } from './logModel';
import styles from './ParityPanels.module.css';


function LogTail({ target, host }: { target: string; host: DesktopHost }) {
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [level, setLevel] = useState('');
  const [search, setSearch] = useState('');
  const [follow, setFollow] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const cursor = useRef(0);
  const file = useRef({ inode: '', size: 0 });
  const list = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => lines.filter((line) => matchesLog(line, level, search)), [lines, level, search]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const params = new URLSearchParams({ target, since: String(cursor.current), follow: follow ? '1' : '0', limit: '500' });
        const response = await fetch(`/logs?${params}`, { signal: controller.signal });
        if (!response.ok) throw new Error(`Log request failed (${response.status})`);
        const page = await response.json() as LogPage;
        if (controller.signal.aborted) return;
        if (!Array.isArray(page.lines) || (page.target && page.target !== target)) throw new Error('Unexpected log response');
        const rotated = file.current.inode && (page.inode !== file.current.inode || Number(page.size) < file.current.size);
        if (rotated && cursor.current) {
          cursor.current = 0;
          file.current = { inode: page.inode || '', size: Number(page.size) || 0 };
          setLines([]);
          // Read the new file from the beginning of its bounded tail.
          timer = setTimeout(() => { void poll(); }, 0);
          return;
        }
        file.current = { inode: page.inode || '', size: Number(page.size) || 0 };
        cursor.current = Number(page.cursor) || cursor.current;
        setLines((previous) => [...previous, ...page.lines].slice(-2000));
        setLoaded(true); setError('');
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : 'Unable to read logs');
      }
      if (follow && !controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 2000);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [target, follow, refresh, reconnect]);

  useLayoutEffect(() => {
    if (follow && list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [visible, follow]);

  return <>
    <div className={styles.toolbar}>
      <label>Level<select value={level} onChange={(event) => setLevel(event.target.value)}><option value="">All levels</option>{['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Search logs<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Regex or text" /></label>
      <label className={styles.check}><input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />Follow</label>
      <Button onPress={() => setRefresh((value) => value + 1)}>Refresh logs</Button>
      {hasHostCapability(host, 'reveal-log-directory') ? <Button onPress={() => { void host.revealLogDirectory().catch((cause: unknown) => setError(String(cause))); }}>Reveal log folder</Button> : null}
      <span>{visible.length} / {lines.length} lines · retains latest 2,000</span>
    </div>
    {error ? <p role="alert">{error}. Use Refresh logs to retry.</p> : null}
    <div ref={list} className={styles.logLines} role="log" aria-label={`${target} log lines`} aria-live={follow ? 'polite' : 'off'}>
      {!visible.length ? <p>{loaded ? 'No log lines match the current filters.' : error ? 'Logs unavailable.' : 'Loading logs…'}</p> : visible.map((line, index) => <div key={`${line.ts}-${index}`} data-level={line.level}><time>{line.ts ? new Date(line.ts * 1000).toLocaleTimeString() : ''}</time><strong>{line.level || ''}</strong><pre>{line.message || line.raw || ''}</pre></div>)}
    </div>
  </>;
}

export function LogViewer({ host }: { host: DesktopHost }) {
  const [target, setTarget] = useState('daemon');
  return <section className={styles.panel} aria-label="Torque logs">
    <header className={styles.toolbar}><h2>Torque logs</h2><label>Log target<select value={target} onChange={(event) => setTarget(event.target.value)}><option value="daemon">Daemon</option><option value="supervisor">Supervisor</option></select></label></header>
    <LogTail key={target} target={target} host={host} />
  </section>;
}
