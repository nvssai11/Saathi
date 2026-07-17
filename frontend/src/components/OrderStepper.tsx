const STEPS = ["Received", "Processing", "Confirmed", "In Production", "Quality Check", "Finalising", "Delivered"];

export default function OrderStepper({ status }: { status: string }) {
  const currentIndex = STEPS.indexOf(status);

  return (
    <div className="stepper">
      {STEPS.map((label, i) => {
        const state = currentIndex < 0 ? "" : i < currentIndex ? "done" : i === currentIndex ? "current" : "";
        return (
          <div key={label} className={`stepper-step ${state}`}>
            <span className="stepper-line" />
            <span className="stepper-dot">{state === "done" ? "✓" : i + 1}</span>
            <span className="stepper-label">{label}</span>
          </div>
        );
      })}
    </div>
  );
}
