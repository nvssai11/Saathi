import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { HelpCircleIcon } from "./icons";

interface InfoTipProps {
  text: string;
}

export default function InfoTip({ text }: InfoTipProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function handleOutside(e: MouseEvent | KeyboardEvent) {
      if (e instanceof KeyboardEvent) {
        if (e.key === "Escape") setOpen(false);
        return;
      }
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("click", handleOutside, true);
    document.addEventListener("keydown", handleOutside);
    return () => {
      document.removeEventListener("click", handleOutside, true);
      document.removeEventListener("keydown", handleOutside);
    };
  }, [open]);

  return (
    <span className="info-tip" ref={rootRef}>
      <button
        type="button"
        className="info-tip-trigger"
        aria-expanded={open}
        aria-label={t("common.whatDoesThisMean")}
        onClick={() => setOpen((o) => !o)}
      >
        <HelpCircleIcon />
      </button>
      {open && (
        <span className="info-tip-bubble" role="tooltip">
          {text}
        </span>
      )}
    </span>
  );
}
