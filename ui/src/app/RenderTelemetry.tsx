import { Profiler, useCallback, useEffect, useState, type ProfilerOnRenderCallback, type ReactNode } from 'react';
import { useAppSelector } from './hooks';
import { RenderWindow, startRenderReports } from './frontendRenderMetrics';

export function RenderTelemetry({ children }: { children: ReactNode }) {
  const [samples] = useState(() => new RenderWindow());
  const connected = useAppSelector((state) => state.connection.status === 'connected');
  const onRender = useCallback<ProfilerOnRenderCallback>((_id, _phase, actualDuration, _baseDuration, _startTime, commitTime) => {
    samples.record(actualDuration, commitTime);
  }, [samples]);
  useEffect(() => connected ? startRenderReports(samples) : undefined, [connected, samples]);
  return <Profiler id="workspace" onRender={onRender}>{children}</Profiler>;
}
