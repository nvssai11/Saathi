interface ResizeOptions {
  maxDimension: number;
  quality: number;
}

async function resizeAndEncode(file: File, { maxDimension, quality }: ResizeOptions): Promise<File> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return file;
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", quality)
  );
  if (!blob || blob.size >= file.size) return file;

  const stem = file.name.replace(/\.\w+$/, "");
  return new File([blob], `${stem}.jpg`, { type: "image/jpeg" });
}

const BACKEND_MAX_BYTES = 10 * 1024 * 1024;

export async function ensureUnderBackendLimit(file: File): Promise<File> {
  if (file.size <= BACKEND_MAX_BYTES || !file.type.startsWith("image/")) {
    return file;
  }
  try {
    return await resizeAndEncode(file, { maxDimension: 2560, quality: 0.92 });
  } catch {
    return file;
  }
}

export async function compressForRetry(file: File): Promise<File> {
  if (!file.type.startsWith("image/")) {
    return file;
  }
  try {
    return await resizeAndEncode(file, { maxDimension: 1600, quality: 0.75 });
  } catch {
    return file;
  }
}
