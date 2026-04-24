import { fmtNumber } from './ui';

/**
 * Raw-SVG 14-day posts-per-day bar chart. No charting library.
 * Fills missing days with zero.
 */
export function Sparkline({
  data,
  days,
  width = 420,
  height = 64,
}: {
  data: { date: string; count: number }[];
  days: number;
  width?: number;
  height?: number;
}) {
  const today = new Date();
  const buckets: { date: string; count: number }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(today.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    const match = data.find((p) => p.date === key);
    buckets.push({ date: key, count: match?.count ?? 0 });
  }

  const max = Math.max(1, ...buckets.map((b) => b.count));
  const total = buckets.reduce((s, b) => s + b.count, 0);
  const barW = width / buckets.length;
  const gap = Math.min(barW * 0.25, 3);

  return (
    <div>
      <div className="flex items-end justify-between mb-2">
        <div>
          <div className="label">Posts / day ({days}d)</div>
          <div className="tabular text-xl font-semibold mt-1">
            {fmtNumber(total)} <span className="text-muted text-sm font-normal">total</span>
          </div>
        </div>
        <div className="text-xs text-muted tabular">
          peak {fmtNumber(max)}
        </div>
      </div>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="w-full">
        {buckets.map((b, i) => {
          const h = Math.round((b.count / max) * (height - 6));
          return (
            <g key={b.date}>
              <rect
                x={i * barW + gap / 2}
                y={height - h}
                width={barW - gap}
                height={h}
                rx={1.5}
                className="fill-accent"
                opacity={b.count === 0 ? 0.15 : 0.75}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
