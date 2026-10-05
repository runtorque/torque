export interface PipelineEdge { from: string; to: string; when: string }
export interface Pipeline { name: string; actions: string[]; edges: PipelineEdge[]; asks: { from: string; when: string }[] }
export interface PipelineNode { name: string; x: number; y: number }

/** Reject incomplete discovery payloads before they can replace a readable graph. */
export function parsePipelines(value: unknown): Pipeline[] | null {
  const object = (item: unknown): Record<string, unknown> => item && typeof item === 'object' && !Array.isArray(item) ? item as Record<string, unknown> : {};
  if (!Array.isArray(value)) return null;
  const valid = value.every((raw: unknown) => {
    const item = object(raw);
    return typeof item.name === 'string' && item.name.length > 0
      && Array.isArray(item.actions) && item.actions.every((name: unknown) => typeof name === 'string')
      && Array.isArray(item.edges) && item.edges.every((raw: unknown) => { const edge = object(raw); return typeof edge.from === 'string' && typeof edge.to === 'string' && typeof edge.when === 'string'; })
      && Array.isArray(item.asks) && item.asks.every((raw: unknown) => { const ask = object(raw); return typeof ask.from === 'string' && typeof ask.when === 'string'; });
  });
  return valid ? value as Pipeline[] : null;
}

/** Longest dependency depth, with bounded relaxation for review-cycle edges. */
export function layoutPipeline(pipeline: Pipeline): { nodes: PipelineNode[]; width: number; height: number } {
  const names = [...new Set(pipeline.actions)];
  const ranks = new Map<string, number>();
  const visiting = new Set<string>();
  const rank = (name: string): number => {
    if (ranks.has(name)) return ranks.get(name)!;
    if (visiting.has(name)) return 0;
    visiting.add(name);
    const parents = pipeline.edges.filter((edge) => edge.to === name && edge.from !== name && names.includes(edge.from));
    const depth = Math.min(names.length - 1, parents.length ? Math.max(...parents.map((edge) => rank(edge.from) + 1)) : 0);
    visiting.delete(name); ranks.set(name, depth); return depth;
  };
  names.forEach(rank);
  const counts = new Map<number, number>();
  const nodes = names.map((name) => { const depth = ranks.get(name) || 0; const row = counts.get(depth) || 0; counts.set(depth, row + 1); return { name, x: 40 + depth * 250, y: 50 + row * 120 }; });
  return { nodes, width: Math.max(400, ...nodes.map((node) => node.x + 240)), height: Math.max(300, ...nodes.map((node) => node.y + 120)) };
}
