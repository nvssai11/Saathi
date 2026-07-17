const GRADE_STYLE: Record<string, { color: string; bg: string; label: string }> = {
  A: { color: "var(--good)", bg: "var(--good-bg)", label: "Excellent" },
  B: { color: "var(--good)", bg: "var(--good-bg)", label: "Good standing" },
  C: { color: "var(--warning)", bg: "var(--warning-bg)", label: "Needs attention" },
  D: { color: "var(--critical)", bg: "var(--critical-bg)", label: "At risk" },
};

const SIZE = 160;
const STROKE = 14;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export default function ScoreRing({ score, grade }: { score: number; grade: string }) {
  const style = GRADE_STYLE[grade] ?? GRADE_STYLE.D;
  const pct = Math.max(0, Math.min(1, score));
  const offset = CIRCUMFERENCE * (1 - pct);

  return (
    <div className="score-ring-wrap">
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke="var(--border)"
          strokeWidth={STROKE}
        />
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke={style.color}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          style={{ transition: "stroke-dashoffset 0.5s ease" }}
        />
      </svg>
      <div className="score-ring-value">
        <span className="score-ring-pct">{(pct * 100).toFixed(0)}%</span>
        <span className="score-ring-grade" style={{ color: style.color, background: style.bg }}>
          Grade {grade} · {style.label}
        </span>
      </div>
    </div>
  );
}
