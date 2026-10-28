export function StatusTag({ status }: { status: string }) {
  return <span className={`status-tag ${status}`}>{status.replace(/_/g, " ")}</span>;
}
