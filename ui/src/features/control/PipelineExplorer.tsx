import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectAuxiliaryResponseState, selectConnection } from '../../app/store';
import { Button } from '../../design/primitives';
import { readCommand } from '../../protocol/http';
import { layoutPipeline, type Pipeline } from './pipelineModel';
import styles from './ParityPanels.module.css';

function PipelineGraph({ pipeline, onEdit }: { pipeline: Pipeline; onEdit: (name: string) => void }) {
  const layout = useMemo(() => layoutPipeline(pipeline), [pipeline]);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [selected, setSelected] = useState('');
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; startX: number; startY: number } | null>(null);
  const marker = useId().replaceAll(':', '');
  return <>
    <div className={styles.toolbar}><Button aria-label="Zoom out pipeline" onPress={() => setScale((value) => Math.max(.25, value / 1.25))}>−</Button><span>{Math.round(scale * 100)}%</span><Button aria-label="Zoom in pipeline" onPress={() => setScale((value) => Math.min(4, value * 1.25))}>＋</Button><Button onPress={() => { setScale(1); setOffset({ x: 0, y: 0 }); }}>Fit pipeline</Button>{selected ? <Button onPress={() => onEdit(selected)}>Edit {selected}</Button> : null}<small>Drag background to pan · arrows pan · nodes open the action editor</small></div>
    <div className={styles.graph}>
      <svg ref={svg} viewBox={`${-offset.x} ${-offset.y} ${layout.width / scale} ${layout.height / scale}`} tabIndex={0} role="group" aria-label="Pipeline graph"
        onKeyDown={(event) => { if (event.target !== event.currentTarget) return; const moves: Record<string, [number, number]> = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] }; const move = moves[event.key]; if (move) { event.preventDefault(); setOffset((point) => ({ x: point.x + move[0], y: point.y + move[1] })); } }}
        onPointerDown={(event) => { if ((event.target as Element).closest('[role="button"]')) return; drag.current = { x: event.clientX, y: event.clientY, startX: offset.x, startY: offset.y }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (!drag.current || !svg.current) return; const bounds = svg.current.getBoundingClientRect(); const units = Math.max(layout.width / scale / bounds.width, layout.height / scale / bounds.height); setOffset({ x: drag.current.startX + (event.clientX - drag.current.x) * units, y: drag.current.startY + (event.clientY - drag.current.y) * units }); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
        {pipeline.edges.map((edge, index) => { const from = layout.nodes.find((node) => node.name === edge.from); const to = layout.nodes.find((node) => node.name === edge.to); if (!from || !to) return null; const x = from.x + 180; const y = from.y + 25; const backward = to.x <= from.x; return <g key={`${edge.from}-${edge.to}-${index}`}><path d={backward ? `M ${x} ${y} C ${x + 50} ${y + 90}, ${to.x - 50} ${to.y + 100}, ${to.x} ${to.y + 25}` : `M ${x} ${y} L ${to.x} ${to.y + 25}`} markerEnd={`url(#${marker})`}><title>{edge.from} → {edge.to}: {edge.when}</title></path></g>; })}
        {layout.nodes.map((node) => <g key={node.name} role="button" tabIndex={0} aria-label={`Edit action ${node.name}`} aria-pressed={selected === node.name} onFocus={() => setSelected(node.name)} onClick={() => { setSelected(node.name); onEdit(node.name); }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onEdit(node.name); } }}><rect x={node.x} y={node.y} width="180" height="50" rx="6" /><text x={node.x + 10} y={node.y + 28}>{node.name.length > 24 ? `${node.name.slice(0, 23)}…` : node.name}</text><title>{node.name}</title></g>)}
      </svg>
    </div>
    <div className={styles.graphDetails} aria-label="Pipeline transitions"><ul>{pipeline.edges.map((edge, index) => <li key={index}><strong>{edge.from} → {edge.to}</strong>: {edge.when || 'Always'}</li>)}{pipeline.asks.map((ask, index) => <li key={`ask-${index}`}><strong>{ask.from} → Ask operator</strong>: {ask.when || 'When input is needed'}</li>)}</ul></div>
  </>;
}

export function PipelineExplorer({ group, onEdit }: { group: string; onEdit: (name: string) => void }) {
  const dispatch = useAppDispatch();
  const responses = useAppSelector(selectAuxiliaryResponseState);
  const connection = useAppSelector(selectConnection);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  const frame = responses[`pipelines:${group}`] as { pipelines?: Pipeline[] } | undefined;
  const pipelines = frame?.pipelines || [];
  const active = pipelines.find((item) => item.name === selected) ?? pipelines[0];
  useEffect(() => {
    const controller = new AbortController();
    void readCommand({ cmd: 'discover_pipelines', group }, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result.type !== 'pipelines' || !Array.isArray(result.pipelines)) throw new Error('Unexpected pipeline discovery response');
      dispatch(projectionActions.auxiliaryResourceReceived({ ...result, group })); setError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Discovery failed'); });
    return () => controller.abort();
  }, [group, refresh, connection.reconnectCount, dispatch]);
  return <section className={styles.panel} aria-label="Pipeline explorer"><header className={styles.toolbar}><h2>Pipeline explorer</h2><label>Pipeline<select value={active?.name || ''} onChange={(event) => setSelected(event.target.value)}>{pipelines.map((pipeline) => <option key={pipeline.name}>{pipeline.name}</option>)}</select></label><Button onPress={() => setRefresh((value) => value + 1)}>Discover pipelines</Button></header>{error ? <p role="alert">{error}. Discover pipelines to retry.</p> : null}{active ? <PipelineGraph key={`${group}-${active.name}`} pipeline={active} onEdit={onEdit} /> : <p>{frame ? 'No connected action pipelines in this group.' : error ? 'Pipelines unavailable.' : 'Discovering action pipelines…'}</p>}</section>;
}
