import { Component, type ErrorInfo, type ReactNode } from 'react';

import { ErrorState } from './feedback/ErrorState';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches render-time errors anywhere in the app subtree and shows an antd
 * fallback instead of React's blank screen. (main.tsx's bootstrap `.catch`
 * only covers the pre-render MSAL bootstrap; this covers everything after the
 * first paint.) Error boundaries must be class components — there is no hook
 * equivalent for `getDerivedStateFromError`.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surfaced to the console (and, once wired, App Insights) — fail loud.
    console.error('Unhandled render error', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      // A render-time crash is a client-side 500-equivalent — use the shared
      // in-brand error page (its 5xx branch offers Reload).
      return <ErrorState code={500} message={this.state.error.message} />;
    }
    return this.props.children;
  }
}
