import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { RelayConnectionDetails, RelayStatusIndicator } from './RelayStatus';
import { relayConnectionView, relayProbePresentation, statusVisibilityEnabled } from './relayStatusModel';
it.each([['connected', 0, 'success'], ['connecting', 4, 'warning'], ['connecting', 5, 'danger'], ['disconnected', 5, 'danger'], ['disconnected', 1, 'warning'], ['error', 0, 'danger'], ['disabled', 0, 'muted'], ['future_state', 9, 'warning']] as const)('represents %s at %s retries without changing the state', (status, count, tone) => {
  const view = relayConnectionView({ status, retry_count: count }); expect(view).toMatchObject({ status, tone, retryCount: count });
});
it('hides absent data, normalizes malformed retry counts and respects explicit configured flags', () => {
  expect(relayConnectionView(null)).toBeNull(); expect(relayConnectionView({})).toBeNull();
  for (const count of [-1, Infinity, 'bad', undefined]) expect(relayConnectionView({ status: 'connecting', retry_count: count })?.retryCount).toBe(0);
  expect(relayConnectionView({ status: 'connected', configured: false, enabled: true })?.configured).toBe(false);
  expect(relayConnectionView({ status: 'disabled', configured: true, enabled: false })?.configured).toBe(true);
  expect(relayConnectionView({ status: 'disabled' })?.configured).toBe(false);
  expect(relayConnectionView({ status: 'error' })?.configured).toBe(true);
});
it('honors status visibility, renders passive accessible context and retains its DOM/focus as severity changes', () => {
  const row = { status: 'connecting', enabled: true, relay_host: 'relay.example', retry_count: 4, last_connected_at: '2030-01-01T00:00:00Z', since: '2030-01-01T00:01:00Z', last_error: 'Connection refused' };
  const { rerender } = render(<RelayStatusIndicator connection={row} visible={false} />); expect(screen.queryByRole('img')).not.toBeInTheDocument();
  rerender(<RelayStatusIndicator connection={row} visible="false" />); expect(screen.queryByRole('img')).not.toBeInTheDocument();
  rerender(<RelayStatusIndicator connection={row} visible="true" />); const indicator = screen.getByRole('img', { name: /Relay: connecting/ }); indicator.focus(); expect(indicator).toHaveAttribute('data-tone', 'warning'); expect(indicator).toHaveAttribute('title', expect.stringContaining('Host: relay.example'));
  rerender(<RelayStatusIndicator connection={{ ...row, retry_count: 5 }} visible />); expect(screen.getByRole('img')).toBe(indicator); expect(indicator).toHaveFocus(); expect(indicator).toHaveAttribute('data-state', 'connecting'); expect(indicator).toHaveAttribute('data-tone', 'danger'); expect(indicator).toHaveAttribute('aria-label', expect.stringContaining('Retrying, 5 attempts')); expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<RelayStatusIndicator connection={{ ...row, configured: false }} visible />); expect(screen.queryByRole('img')).not.toBeInTheDocument();
  for (const value of [true, '1', 'true', 'YES', 'on']) expect(statusVisibilityEnabled(value)).toBe(true);
  for (const value of [false, '', 'false', '0', undefined]) expect(statusVisibilityEnabled(value)).toBe(false);
});
it('shows readable live fields, machine-readable valid dates, raw invalid dates and escaped errors', () => {
  const { rerender } = render(<RelayConnectionDetails connection={{}} />); expect(screen.queryByRole('region')).not.toBeInTheDocument();
  rerender(<RelayConnectionDetails connection={{ status: 'disconnected', relay_host: 'relay.example', daemon_id: 'daemon-1', retry_count: 7, since: '2030-01-01T00:00:00Z', last_connected_at: 'unavailable timestamp', last_error: '<script>literal error</script>' }} />);
  expect(screen.getByRole('region', { name: 'Relay connection' })).toHaveTextContent('Repeated retries need attention'); expect(screen.getByText('relay.example')).toBeVisible(); expect(screen.getByText('daemon-1')).toBeVisible(); expect(screen.getByText('7')).toBeVisible();
  expect(screen.getByText('2030-01-01T00:00:00Z')).toHaveAttribute('datetime', '2030-01-01T00:00:00.000Z'); expect(screen.getByText('unavailable timestamp').tagName).toBe('DD'); expect(screen.getByText('<script>literal error</script>')).toBeVisible(); expect(document.querySelector('script')).toBeNull();
});
it.each([['ok', 'success'], ['reachable_unauthed', 'warning'], ['disabled', 'muted'], ['ca_missing', 'danger'], ['unreachable', 'danger'], ['auth_rejected', 'danger'], ['misconfigured', 'danger'], ['future_result', 'warning']] as const)('retains probe severity for %s', (status, tone) => { expect(relayProbePresentation(status).tone).toBe(tone); if (status === 'future_result') expect(relayProbePresentation(status).label).toBe(status); });
