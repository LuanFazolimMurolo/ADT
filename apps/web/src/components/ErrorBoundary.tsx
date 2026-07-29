import { Component, type ReactNode } from 'react'

interface State {
  failed: boolean
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch() {
    // Intentionally do not log arbitrary errors: they may contain sensitive context.
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="state state--full" role="alert">
          <p className="eyebrow">ADT / erro de interface</p>
          <h1>Não foi possível exibir esta página</h1>
          <p>Recarregue a página. Se o problema continuar, verifique a conexão com os serviços.</p>
          <button type="button" onClick={() => window.location.reload()}>Recarregar</button>
        </main>
      )
    }
    return this.props.children
  }
}
