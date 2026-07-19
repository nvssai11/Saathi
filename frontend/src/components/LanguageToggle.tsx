import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES, SupportedLanguage } from "../i18n";

const LABELS: Record<SupportedLanguage, string> = {
  en: "EN",
  hi: "हिं",
};

export default function LanguageToggle() {
  const { i18n, t } = useTranslation();
  const current = (i18n.resolvedLanguage ?? "en") as SupportedLanguage;

  return (
    <div className="lang-toggle" role="group" aria-label={t("common.chooseLanguage")}>
      {SUPPORTED_LANGUAGES.map((lng) => (
        <button
          key={lng}
          type="button"
          className={lng === current ? "active" : ""}
          aria-pressed={lng === current}
          onClick={() => i18n.changeLanguage(lng)}
        >
          {LABELS[lng]}
        </button>
      ))}
    </div>
  );
}
