import { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
  showDetails: boolean
  copied: boolean
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      showDetails: false,
      copied: false,
    }
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Unhandled React Error in ErrorBoundary:', error, errorInfo)
    this.setState({ errorInfo })
  }

  handleReload = () => {
    window.location.reload()
  }

  handleClearCacheAndReload = () => {
    try {
      localStorage.clear()
      sessionStorage.clear()
    } catch {
      // ignore
    }
    window.location.reload()
  }

  handleCopy = () => {
    const { error, errorInfo } = this.state
    const text = [
      `Error: ${error?.message || 'Unknown error'}`,
      `Stack:\n${error?.stack || 'No stack trace'}`,
      `Component Stack:\n${errorInfo?.componentStack || 'No component stack'}`,
    ].join('\n\n')

    navigator.clipboard?.writeText(text).then(() => {
      this.setState({ copied: true })
      setTimeout(() => this.setState({ copied: false }), 2000)
    })
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      const isZh = (() => {
        try {
          return localStorage.getItem('agent_fleet_lang') !== 'en'
        } catch {
          return true
        }
      })()

      const { error, errorInfo, showDetails, copied } = this.state

      return (
        <div
          role="alert"
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: 'var(--bg, #0e1116)',
            color: 'var(--text, #d8dee9)',
            padding: 24,
            boxSizing: 'border-box',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          }}
        >
          <div
            style={{
              maxWidth: 640,
              width: '100%',
              backgroundColor: 'var(--panel, #161b22)',
              border: '1px solid var(--border, #2a3140)',
              borderRadius: 12,
              padding: '32px 28px',
              boxShadow: '0 8px 32px rgba(0, 0, 0, 0.45)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 10,
                  backgroundColor: 'rgba(248, 81, 73, 0.15)',
                  border: '1px solid rgba(248, 81, 73, 0.3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 22,
                  color: 'var(--bad, #f85149)',
                }}
              >
                ⚠️
              </div>
              <div>
                <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#f0f6fc' }}>
                  {isZh ? '前端界面遇到未捕获异常' : 'Application Runtime Error'}
                </h1>
                <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--muted, #8b95a6)' }}>
                  {isZh
                    ? '界面发生意外错误。您可以尝试刷新页面，或清空本地缓存后重试。'
                    : 'An unhandled React exception occurred. You can reload or reset your browser state.'}
                </p>
              </div>
            </div>

            {error?.message && (
              <div
                style={{
                  background: 'rgba(248, 81, 73, 0.08)',
                  border: '1px solid rgba(248, 81, 73, 0.25)',
                  borderRadius: 8,
                  padding: '10px 14px',
                  color: 'var(--bad, #f85149)',
                  fontSize: 13,
                  fontFamily: 'ui-monospace, monospace',
                  wordBreak: 'break-word',
                  marginBottom: 16,
                }}
              >
                <strong>Error: </strong>
                {error.message}
              </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 16 }}>
              <button
                type="button"
                className="btn primary"
                onClick={this.handleReload}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
              >
                <span>🔄</span>
                <span>{isZh ? '重新加载页面' : 'Reload Application'}</span>
              </button>
              <button
                type="button"
                className="btn"
                onClick={this.handleClearCacheAndReload}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  borderColor: 'rgba(210, 153, 34, 0.4)',
                  color: 'var(--warn, #d29922)',
                }}
              >
                <span>🧹</span>
                <span>{isZh ? '清空缓存并重载' : 'Clear Cache & Reload'}</span>
              </button>
              <button
                type="button"
                className="btn small"
                onClick={this.handleCopy}
                style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5 }}
              >
                <span>{copied ? '✅' : '📋'}</span>
                <span>{copied ? (isZh ? '已复制' : 'Copied') : (isZh ? '复制详情' : 'Copy Details')}</span>
              </button>
            </div>

            {/* Collapsible stack trace */}
            <div style={{ borderTop: '1px solid var(--border, #2a3140)', paddingTop: 12 }}>
              <button
                type="button"
                onClick={() => this.setState({ showDetails: !showDetails })}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--accent, #4c9aff)',
                  fontSize: 12,
                  cursor: 'pointer',
                  padding: 0,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                <span>{showDetails ? '▼' : '▶'}</span>
                <span>
                  {showDetails
                    ? isZh
                      ? '收起详细堆栈'
                      : 'Hide Details'
                    : isZh
                    ? '展开错误堆栈详情'
                    : 'View Error Stack Details'}
                </span>
              </button>

              {showDetails && (
                <div
                  style={{
                    marginTop: 10,
                    background: 'var(--panel-2, #1c2230)',
                    border: '1px solid var(--border, #2a3140)',
                    borderRadius: 6,
                    padding: 12,
                    maxHeight: 260,
                    overflowY: 'auto',
                    fontSize: 11,
                    fontFamily: 'ui-monospace, monospace',
                    lineHeight: 1.5,
                    color: '#c9d1d9',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                  }}
                >
                  {error?.stack && (
                    <div style={{ marginBottom: 12 }}>
                      <strong style={{ color: 'var(--accent, #4c9aff)' }}>Stack Trace:</strong>
                      <div>{error.stack}</div>
                    </div>
                  )}
                  {errorInfo?.componentStack && (
                    <div>
                      <strong style={{ color: 'var(--warn, #d29922)' }}>Component Stack:</strong>
                      <div>{errorInfo.componentStack}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
