import DaemonsView, { RuntimeDaemonsSummary } from './DaemonsView'

export function PaseoRuntimeSummary({
  runtimeId,
  refreshSignal,
}: {
  runtimeId: string
  refreshSignal?: number
}) {
  return <RuntimeDaemonsSummary runtimeId={runtimeId} refreshSignal={refreshSignal} />
}

export default function PaseoView(props: {
  refreshSignal?: number
  setToast?: (message: string) => void
  runtimeId?: string
  compact?: boolean
}) {
  return <DaemonsView {...props} serviceFilterDefault="all" />
}
