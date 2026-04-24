'use client';

import { useRef, useTransition } from 'react';
import { toast } from 'sonner';

type ActionResult = { ok: boolean; message: string } | void;

/**
 * Thin wrapper around a server action so we can toast success/failure.
 * Use instead of `<form action={serverAction}>` when you want a toast.
 */
export function ActionForm({
  action,
  onSuccess,
  resetOnSuccess = true,
  className,
  children,
}: {
  action: (fd: FormData) => Promise<ActionResult>;
  onSuccess?: () => void;
  resetOnSuccess?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLFormElement | null>(null);
  const [pending, startTransition] = useTransition();

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    startTransition(async () => {
      const r = await action(fd);
      if (r && 'message' in r) {
        (r.ok ? toast.success : toast.error)(r.message);
        if (r.ok) {
          if (resetOnSuccess) ref.current?.reset();
          onSuccess?.();
        }
      }
    });
  }

  return (
    <form
      ref={ref}
      onSubmit={handleSubmit}
      className={className}
      data-pending={pending || undefined}
    >
      {children}
    </form>
  );
}
