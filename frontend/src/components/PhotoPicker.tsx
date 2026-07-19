import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ensureUnderBackendLimit } from "../utils/imageCompress";

interface PhotoPickerProps {
  id: string;
  photo: File | null;
  onChange: (file: File | null) => void;
  required?: boolean;
}

export default function PhotoPicker({ id, photo, onChange, required }: PhotoPickerProps) {
  const { t } = useTranslation();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [compressing, setCompressing] = useState(false);

  useEffect(() => {
    if (!photo) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(photo);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  async function handleFileSelected(file: File | null) {
    if (!file) {
      onChange(null);
      return;
    }
    setCompressing(true);
    try {
      onChange(await ensureUnderBackendLimit(file));
    } finally {
      setCompressing(false);
    }
  }

  if (compressing) {
    return <span className="muted">{t("photoPicker.checkingPhoto")}</span>;
  }

  if (!photo) {
    return (
      <input
        id={id}
        type="file"
        accept="image/*"
        required={required}
        onChange={(e) => handleFileSelected(e.target.files?.[0] ?? null)}
      />
    );
  }

  return (
    <div className="photo-picker-preview">
      {previewUrl && <img src={previewUrl} alt="" />}
      <span className="photo-picker-name">{photo.name}</span>
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(null)}>
        {t("photoPicker.remove")}
      </button>
    </div>
  );
}
