import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex items-center justify-center h-full min-h-[200px]">
          <div className="card max-w-lg w-full text-center space-y-3 p-8">
            <div className="text-red-400 font-semibold text-lg">Page Error</div>
            <div className="text-gray-400 text-sm font-mono break-all">
              {this.state.error.message}
            </div>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-2 px-4 py-2 rounded bg-brand-500 text-white text-sm hover:bg-brand-600 transition-colors"
            >
              Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
