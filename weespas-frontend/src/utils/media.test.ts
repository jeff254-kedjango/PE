import { describe, it, expect } from 'vitest';
import { isVideoUrl } from './media';

describe('isVideoUrl', () => {
  it('detects videos by the trade pipeline path segment', () => {
    expect(isVideoUrl('/uploads/trade/videos/clip.mp4')).toBe(true);
    expect(isVideoUrl('/uploads/trade/images/photo.png')).toBe(false);
  });

  it('falls back to the file extension for external/legacy urls', () => {
    expect(isVideoUrl('https://cdn.example.com/a.webm')).toBe(true);
    expect(isVideoUrl('https://cdn.example.com/a.mov')).toBe(true);
    expect(isVideoUrl('https://cdn.example.com/a.jpg')).toBe(false);
  });

  it('ignores query/hash when checking the extension', () => {
    expect(isVideoUrl('/x/clip.mp4?token=abc#t=1')).toBe(true);
    expect(isVideoUrl('/x/photo.png?v=2')).toBe(false);
  });

  it('is false for null/blank', () => {
    expect(isVideoUrl(null)).toBe(false);
    expect(isVideoUrl(undefined)).toBe(false);
    expect(isVideoUrl('')).toBe(false);
  });

  it('prefers the path segment over a misleading extension', () => {
    // A video path wins even if the URL ends oddly; an image path is never a video.
    expect(isVideoUrl('/uploads/trade/images/weird.mp4-thumb.png')).toBe(false);
  });
});
