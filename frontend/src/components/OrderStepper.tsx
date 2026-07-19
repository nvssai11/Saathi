import { useTranslation } from "react-i18next";
import { translateBuyerStatus } from "../utils/format";

const STEPS = ["Received", "Processing", "Confirmed", "In Production", "Quality Check", "Finalising", "Delivered"];

export default function OrderStepper({ status }: { status: string }) {
  const { t } = useTranslation();
  const currentIndex = STEPS.indexOf(
    status === "Delivered — with quality issues" ? "Delivered" : status
  );

  return (
    <div className="stepper">
      {STEPS.map((label, i) => {
        const state = currentIndex < 0 ? "" : i < currentIndex ? "done" : i === currentIndex ? "current" : "";
        return (
          <div key={label} className={`stepper-step ${state}`}>
            <span className="stepper-line" />
            <span className="stepper-dot">{state === "done" ? "✓" : i + 1}</span>
            <span className="stepper-label">{translateBuyerStatus(label, t)}</span>
          </div>
        );
      })}
    </div>
  );
}
