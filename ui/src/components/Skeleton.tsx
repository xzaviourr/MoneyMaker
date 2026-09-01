import { cn } from '../lib/utils'

/** Animated shimmer bar — use in place of "Loading…" text. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={cn('animate-pulse rounded bg-gray-800', className)}
    />
  )
}

/** Full-page table skeleton: N shimmer rows matching the table layout. */
export function TableSkeleton({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div role="status" aria-label="Loading data" className="space-y-2 p-4">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton
              key={c}
              className={cn('h-5 rounded', c === 0 ? 'w-24' : c === cols - 1 ? 'w-16' : 'flex-1')}
            />
          ))}
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  )
}

/** Card-level skeleton for stat / summary cards. */
export function CardSkeleton({ lines = 2 }: { lines?: number }) {
  return (
    <div role="status" aria-label="Loading" className="card space-y-3">
      <Skeleton className="h-3 w-1/3" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={cn('h-5', i === 0 ? 'w-2/3' : 'w-1/2')} />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  )
}
