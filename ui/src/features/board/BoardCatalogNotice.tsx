import { Button } from '../../design/primitives';
export function BoardCatalogNotice({ catalog }: { catalog: { error: string; loading: boolean; retry: () => void } }) {
  if (catalog.error) return <p role="alert">{catalog.error} <Button onPress={catalog.retry}>Retry task options</Button></p>;
  return catalog.loading ? <p role="status">Loading task options…</p> : null;
}
