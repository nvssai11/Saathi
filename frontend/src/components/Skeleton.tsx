export function SkeletonTable({ rows = 4, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="card table-card">
      <div className="table-scroll">
        <table>
          <tbody>
            {Array.from({ length: rows }).map((_, r) => (
              <tr key={r}>
                {Array.from({ length: cols }).map((_, c) => (
                  <td key={c}>
                    <span className="skeleton skeleton-line" />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card">
      <span className="skeleton skeleton-line" style={{ width: "35%", height: "1.1rem", marginBottom: "0.9rem" }} />
      {Array.from({ length: lines }).map((_, i) => (
        <span
          key={i}
          className="skeleton skeleton-line"
          style={{ width: i === lines - 1 ? "55%" : "90%" }}
        />
      ))}
    </div>
  );
}
