'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Users,
  FileText,
  History,
  SlidersHorizontal,
  Sparkles,
  PlayCircle,
} from 'lucide-react';

import { cn } from '@/lib/cn';

const ITEMS: { href: string; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { href: '/', label: 'Overview', icon: LayoutDashboard },
  { href: '/creators', label: 'Creators', icon: Users },
  { href: '/posts', label: 'Posts', icon: FileText },
  { href: '/runs', label: 'Runs', icon: History },
  { href: '/analysis', label: 'Analysis', icon: SlidersHorizontal },
  { href: '/skill', label: 'Skill', icon: Sparkles },
  { href: '/actions', label: 'Actions', icon: PlayCircle },
];

export function Sidebar() {
  const pathname = usePathname();
  const [host, setHost] = useState<string | null>(null);
  useEffect(() => {
    setHost(window.location.host);
  }, []);
  return (
    <aside className="w-56 shrink-0 border-r border-border bg-panel/60 backdrop-blur">
      <div className="sticky top-0 flex flex-col h-screen">
        <div className="px-5 pt-8 pb-6">
          <div className="flex items-center gap-2">
            <div className="size-2 rounded-full bg-accent" />
            <span className="font-semibold tracking-tight">LinkedIn Analyzer</span>
          </div>
          <div className="mt-1 text-[10px] uppercase tracking-[0.18em] text-dim">
            Control Panel
          </div>
        </div>
        <nav className="flex-1 px-3 space-y-0.5">
          {ITEMS.map((item) => {
            const active =
              item.href === '/' ? pathname === '/' : pathname?.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
                  'hover:bg-panel-2 hover:text-text',
                  active ? 'bg-panel-2 text-text' : 'text-muted',
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    'absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[2px] rounded-r bg-accent transition-opacity',
                    active ? 'opacity-100' : 'opacity-0',
                  )}
                />
                <Icon className={cn('size-4', active ? 'text-accent' : 'text-dim')} />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="p-4 text-[11px] text-dim">
          <div className="tabular">{host ?? '—'}</div>
          <div className="mt-1">local-only · unauthenticated</div>
        </div>
      </div>
    </aside>
  );
}
