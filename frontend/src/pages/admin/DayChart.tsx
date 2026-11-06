import { useState } from "react";
import type { OverviewDay } from "./api";

export interface DaySeries {
  key: "done" | "failed" | "minutes" | "signups";
  label: string;
  color: string;
}

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

/** The smallest clean top for the axis. Every step but 1 is even, so the middle tick is whole too. */
function niceTop(max: number): number {
  if (max <= 1) return 1;
  const unit = 10 ** Math.floor(Math.log10(max));
  const step = [1, 2, 4, 6, 8, 10].find((s) => s * unit >= max) ?? 10;
  return step * unit;
}

/** Columns over the last days, one axis, stacked when there's more than one series. Hover or arrow
 * keys read a day out; the table below gives every number without either. */
export function DayChart({
  title,
  note,
  days,
  series,
  empty,
  digits = 0,
}: {
  title: string;
  note?: string;
  days: OverviewDay[];
  series: DaySeries[];
  empty: string;
  digits?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const total = (d: OverviewDay) => series.reduce((sum, s) => sum + d[s.key], 0);
  const top = niceTop(Math.max(0, ...days.map(total)));
  const hasData = days.some((d) => total(d) > 0);
  const fmt = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: digits });
  const shown = active === null ? null : days[active];
  // keeps the readout inside the plot at both ends
  const side = active === null ? "" : active < 3 ? "from-left" : active > days.length - 4 ? "from-right" : "";

  return (
    <figure className="dc">
      <figcaption>
        <div className="dc-title">{title}</div>
        {note && <div className="dc-note">{note}</div>}
      </figcaption>

      {!hasData ? (
        <p className="dc-empty">{empty}</p>
      ) : (
        <>
          {series.length > 1 && (
            <ul className="dc-legend">
              {series.map((s) => (
                <li key={s.key}><span aria-hidden style={{ background: s.color }} />{s.label}</li>
              ))}
            </ul>
          )}
          <div className="dc-chart">
            <div className="dc-y" aria-hidden>
              <span>{fmt(top)}</span>
              {top > 1 && <span>{fmt(top / 2)}</span>}
              <span>0</span>
            </div>
            <div
              className="dc-plot"
              role="group"
              tabIndex={0}
              aria-label={`${title}. Use the arrow keys to read each day, or open the table below.`}
              onFocus={() => setActive((a) => a ?? days.length - 1)}
              onBlur={() => setActive(null)}
              onPointerLeave={() => setActive(null)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                e.preventDefault();
                const step = e.key === "ArrowLeft" ? -1 : 1;
                setActive((a) => Math.min(days.length - 1, Math.max(0, (a ?? days.length - 1) + step)));
              }}
            >
              <i className="dc-line" style={{ top: 0 }} />
              {top > 1 && <i className="dc-line" style={{ top: "50%" }} />}
              <i className="dc-line" style={{ bottom: 0 }} />
              <div className="dc-cols">
                {days.map((d, i) => (
                  <div key={d.date} className={`dc-col${active === i ? " on" : ""}`} onPointerEnter={() => setActive(i)}>
                    {series.map((s) =>
                      d[s.key] > 0 ? (
                        <span key={s.key} className="dc-seg" style={{ height: `${(d[s.key] / top) * 100}%`, background: s.color }} />
                      ) : null,
                    )}
                  </div>
                ))}
              </div>
              {shown && active !== null && (
                <div className={`dc-tip ${side}`} style={{ left: `${((active + 0.5) / days.length) * 100}%` }}>
                  <div className="dc-tip-day">{day(shown.date)}</div>
                  {series.map((s) => (
                    <div key={s.key} className="dc-tip-row">
                      <i aria-hidden style={{ background: s.color }} />
                      <b>{fmt(shown[s.key])}</b>
                      <span>{s.label}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="dc-x" aria-hidden>
              <span>{day(days[0].date)}</span>
              <span>{day(days[days.length - 1].date)}</span>
            </div>
          </div>
          <details className="dc-table">
            <summary>Table</summary>
            <div className="table-wrap">
              <table className="admin-table" style={{ minWidth: 0 }}>
                <thead>
                  <tr><th>Day</th>{series.map((s) => <th key={s.key}>{s.label}</th>)}</tr>
                </thead>
                <tbody>
                  {[...days].reverse().map((d) => (
                    <tr key={d.date}><td>{day(d.date)}</td>{series.map((s) => <td key={s.key}>{fmt(d[s.key])}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </figure>
  );
}
