import React, { useRef, useEffect, useCallback } from 'react';
import './HeroAnimation.css';

const PATH_DATA =
  'M 40,200 C 80,180 120,100 180,120 S 260,200 300,160 S 380,60 340,140 S 240,220 280,280 S 380,300 350,240 S 280,160 220,200 S 140,280 100,240 S 60,160 40,200';

const PINS = [
  { x: 180, y: 120, size: 'lg' },
  { x: 300, y: 160, size: 'sm' },
  { x: 340, y: 140, size: 'lg' },
  { x: 100, y: 240, size: 'sm' },
  { x: 280, y: 280, size: 'lg' },
  { x: 220, y: 200, size: 'sm' },
] as const;

const BLOCKS = [
  { x: 60, y: 80, w: 80, h: 50 },
  { x: 200, y: 60, w: 100, h: 60 },
  { x: 160, y: 180, w: 70, h: 80 },
  { x: 280, y: 200, w: 60, h: 70 },
  { x: 80, y: 220, w: 50, h: 60 },
  { x: 320, y: 100, w: 50, h: 40 },
];

const HeroAnimation: React.FC = () => {
  const carRef = useRef<SVGGElement>(null);
  const pathRef = useRef<SVGPathElement>(null);
  const animationRef = useRef<number>(0);
  const progressRef = useRef<number>(0);

  const animate = useCallback(() => {
    if (!pathRef.current || !carRef.current) return;

    const pathLength = pathRef.current.getTotalLength();
    progressRef.current = (progressRef.current + 0.4) % pathLength;

    const point = pathRef.current.getPointAtLength(progressRef.current);
    const aheadPoint = pathRef.current.getPointAtLength(
      (progressRef.current + 2) % pathLength
    );

    const angle =
      Math.atan2(aheadPoint.y - point.y, aheadPoint.x - point.x) *
      (180 / Math.PI);

    carRef.current.setAttribute(
      'transform',
      `translate(${point.x}, ${point.y}) rotate(${angle})`
    );

    animationRef.current = requestAnimationFrame(animate);
  }, []);

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)'
    ).matches;
    if (!prefersReducedMotion) {
      animationRef.current = requestAnimationFrame(animate);
    }
    return () => cancelAnimationFrame(animationRef.current);
  }, [animate]);

  return (
    <div className="hero-map">
      <svg
        viewBox="0 0 400 340"
        className="hero-map__svg"
        aria-hidden="true"
      >
        <defs>
          <pattern
            id="mapGrid"
            width="40"
            height="40"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 40 0 L 0 0 0 40"
              fill="none"
              stroke="rgba(2,44,34,0.05)"
              strokeWidth="1"
            />
          </pattern>
        </defs>

        {/* Map grid background */}
        <rect width="400" height="340" fill="url(#mapGrid)" />

        {/* Abstract city blocks */}
        {BLOCKS.map((b, i) => (
          <rect
            key={i}
            x={b.x}
            y={b.y}
            width={b.w}
            height={b.h}
            rx="4"
            fill="rgba(2,44,34,0.03)"
          />
        ))}

        {/* Dotted path */}
        <path
          ref={pathRef}
          d={PATH_DATA}
          fill="none"
          stroke="rgba(2,44,34,0.1)"
          strokeWidth="2"
          strokeDasharray="6 4"
        />

        {/* Location pins */}
        {PINS.map((pin, i) =>
          pin.size === 'lg' ? (
            <g key={i} className="hero-map__pin" transform={`translate(${pin.x},${pin.y})`}>
              <circle r="4" fill="var(--color-accent)" />
              <circle
                r="7"
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth="1"
                opacity="0.4"
                className="hero-map__pin-pulse"
              />
            </g>
          ) : (
            <g key={i} transform={`translate(${pin.x},${pin.y})`}>
              <circle r="3" fill="var(--color-primary)" opacity="0.5" />
            </g>
          )
        )}

        {/* Moving marker */}
        <g ref={carRef}>
          <polygon points="-6,-4 6,0 -6,4" fill="var(--color-primary)" />
          <circle r="3" fill="var(--color-accent)" opacity="0.7" />
        </g>
      </svg>
    </div>
  );
};

export default HeroAnimation;
