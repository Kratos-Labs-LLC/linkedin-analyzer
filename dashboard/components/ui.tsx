import * as React from 'react';

import { cn } from '@/lib/cn';

export function Panel({
  title,
  subtitle,
  action,
  children,
  className,
}: {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('panel p-5', className)}>
      {(title || action) && (
        <header className="flex items-start justify-between mb-4 gap-4">
          <div>
            {title ? <h2 className="text-sm font-semibold tracking-tight">{title}</h2> : null}
            {subtitle ? <p className="text-xs text-muted mt-0.5">{subtitle}</p> : null}
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-8 flex items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? <p className="text-sm text-muted mt-1">{subtitle}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  mono = true,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="panel p-4">
      <div className="label">{label}</div>
      <div className={cn('mt-1 text-xl font-semibold', mono && 'tabular')}>{value}</div>
      {hint ? <div className="mt-1 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

export function Pill({
  variant,
  children,
  className,
}: {
  variant:
    | 'success'
    | 'partial'
    | 'failed'
    | 'auth_error'
    | 'anchor'
    | 'standard'
    | 'active'
    | 'inactive';
  children: React.ReactNode;
  className?: string;
}) {
  return <span className={cn('pill', `pill-${variant}`, className)}>{children}</span>;
}

export function Button({
  variant = 'secondary',
  className,
  disabled,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
}) {
  const base =
    'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed';
  const styles = {
    primary:
      'bg-accent text-bg hover:bg-accent-dim focus-visible:outline-2 focus-visible:outline-accent',
    secondary:
      'bg-panel-2 text-text border border-border hover:border-border-strong hover:text-accent',
    danger:
      'bg-panel-2 text-rose border border-rose/40 hover:border-rose hover:bg-rose/10',
    ghost: 'text-muted hover:text-text hover:bg-panel-2',
  } as const;
  return (
    <button
      className={cn(base, styles[variant], className)}
      disabled={disabled}
      {...props}
    />
  );
}

export function LinkButton({
  variant = 'secondary',
  className,
  children,
  href,
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: 'primary' | 'secondary' | 'ghost';
  href: string;
}) {
  const base =
    'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors';
  const styles = {
    primary: 'bg-accent text-bg hover:bg-accent-dim',
    secondary:
      'bg-panel-2 text-text border border-border hover:border-border-strong hover:text-accent',
    ghost: 'text-muted hover:text-text hover:bg-panel-2',
  } as const;
  return (
    <a href={href} className={cn(base, styles[variant], className)} {...props}>
      {children}
    </a>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        'w-full rounded-md bg-panel-2 border border-border px-3 py-1.5 text-sm',
        'text-text placeholder:text-dim',
        'focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent',
        props.className,
      )}
    />
  );
}

export function Select({
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        'rounded-md bg-panel-2 border border-border px-3 py-1.5 text-sm text-text',
        'focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent',
        props.className,
      )}
    >
      {children}
    </select>
  );
}

export function Label({
  children,
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label {...props} className={cn('label block mb-1.5', className)}>
      {children}
    </label>
  );
}

export function Checkbox({
  label,
  ...props
}: { label: React.ReactNode } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
      <input
        type="checkbox"
        {...props}
        className={cn(
          'size-4 rounded border-border bg-panel-2 accent-accent',
          props.className,
        )}
      />
      <span className="text-text">{label}</span>
    </label>
  );
}

export function Dot({
  variant,
  className,
}: {
  variant: 'ok' | 'err' | 'warn' | 'running' | 'dim';
  className?: string;
}) {
  const colors = {
    ok: 'bg-green',
    err: 'bg-rose',
    warn: 'bg-amber',
    running: 'bg-accent pulse-dot',
    dim: 'bg-dim',
  } as const;
  return <span className={cn('size-2 rounded-full inline-block', colors[variant], className)} />;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted">
      {children}
    </div>
  );
}

export function fmtScore(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v.toFixed(4);
}

export function fmtTs(v: string | null | undefined): string {
  if (!v) return '—';
  return v.split('.')[0].replace('T', ' ');
}

export function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString();
}
