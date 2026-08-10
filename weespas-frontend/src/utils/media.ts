import { API_BASE_URL } from '../api/config';

const BACKEND_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '');

export function resolveMediaUrl(url: string | undefined | null): string | undefined {
  if (!url) return undefined;
  if (url.startsWith('http://') || url.startsWith('https://')) return url;
  return `${BACKEND_ORIGIN}${url.startsWith('/') ? '' : '/'}${url}`;
}

// Video file extensions the trade media pipeline accepts (mirrors TradeMediaUploader's VIDEO_ACCEPT).
const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.qt', '.m4v', '.ogv'];

/** Whether a media URL points at a video rather than an image. The trade upload pipeline stores
 *  videos under `/uploads/trade/videos/` and images under `/uploads/trade/images/`, so the path
 *  segment is the primary, reliable signal; the extension check is a fallback for external/legacy
 *  URLs. Pure — drives whether the carousel renders a <video> or an <img> for a given slide. */
export function isVideoUrl(url: string | undefined | null): boolean {
  if (!url) return false;
  const lower = url.toLowerCase().split('?')[0].split('#')[0];
  if (lower.includes('/trade/videos/')) return true;
  if (lower.includes('/trade/images/')) return false;
  return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext));
}
