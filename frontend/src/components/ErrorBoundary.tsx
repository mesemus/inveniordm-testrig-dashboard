import { Alert, Link as MuiLink, Typography } from '@mui/material'
import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Link as RouterLink } from 'react-router'

import { errorText } from '../api/client'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: unknown
}

/** Keeps the AppBar and a way back when one page crashes. `Layout` keys it by pathname. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error('Unhandled UI error', error, info.componentStack)
  }

  render(): ReactNode {
    if (this.state.error !== null) {
      return (
        <>
          <Typography variant="h5" gutterBottom>
            Something went wrong
          </Typography>
          <Alert severity="error" sx={{ mb: 2 }}>
            {errorText(this.state.error)}
          </Alert>
          <MuiLink component={RouterLink} to="/">
            Back to the targets overview
          </MuiLink>
        </>
      )
    }
    return this.props.children
  }
}
