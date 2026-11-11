import { Component, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: unknown) {
    console.error('UI ErrorBoundary caught error:', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <Card className="my-6 p-6 border-rose-500/30 bg-rose-500/5 text-center">
          <div className="flex flex-col items-center gap-3">
            <div className="p-3 rounded-full bg-rose-500/10 text-rose-400">
              <AlertTriangle size={28} />
            </div>
            <h3 className="text-lg font-semibold text-red-200">Произошла ошибка интерфейса</h3>
            <p className="text-xs text-zinc-400 max-w-md font-mono bg-zinc-950/60 p-2.5 rounded-lg border border-white/5 break-words">
              {this.state.error?.message || 'Неизвестная ошибка рендеринга'}
            </p>
            <Button size="sm" variant="secondary" onClick={this.handleReset} className="mt-2">
              <RotateCcw size={14} />
              Перезагрузить компонент
            </Button>
          </div>
        </Card>
      )
    }

    return this.props.children
  }
}
