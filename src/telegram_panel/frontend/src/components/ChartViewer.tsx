import { useState, useEffect, useRef } from 'react';

function ChartSkeleton() {
  return (
    <div className="aspect-[4/3] bg-tg-section-bg rounded-xl p-4 flex flex-col justify-between overflow-hidden relative">
      {/* Shimmer overlay */}
      <div
        className="absolute inset-0 -translate-x-full animate-[shimmer_1.5s_infinite]"
        style={{
          background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.04) 50%, transparent 100%)',
        }}
      />

      {/* Top bar skeleton */}
      <div className="flex items-center justify-between">
        <div className="h-3 w-24 rounded bg-white/5 animate-pulse" />
        <div className="h-3 w-16 rounded bg-white/5 animate-pulse" />
      </div>

      {/* Chart area — fake candlesticks */}
      <div className="flex-1 flex items-end gap-[3px] px-2 py-4">
        {[40, 55, 35, 65, 50, 70, 45, 60, 38, 72, 55, 48, 62, 42, 58, 68, 44, 52, 66, 36, 56, 46, 64, 50].map((h, i) => (
          <div
            key={i}
            className="flex-1 rounded-sm animate-pulse"
            style={{
              height: `${h}%`,
              backgroundColor: i % 3 === 0 ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
              animationDelay: `${i * 60}ms`,
            }}
          />
        ))}
      </div>

      {/* Bottom axis skeleton */}
      <div className="flex justify-between">
        <div className="h-2 w-10 rounded bg-white/5 animate-pulse" />
        <div className="h-2 w-10 rounded bg-white/5 animate-pulse" />
        <div className="h-2 w-10 rounded bg-white/5 animate-pulse" />
        <div className="h-2 w-10 rounded bg-white/5 animate-pulse" />
      </div>
    </div>
  );
}

interface ChartViewerProps {
  imageUrl: string;
  alt?: string;
}

/** Strip cache-buster (&t=N) to get the stable symbol portion of the URL. */
function baseUrl(url: string) {
  return url.replace(/[&?]t=\d+/, '');
}

export function ChartViewer({ imageUrl, alt = 'Chart' }: ChartViewerProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const prevBase = useRef(baseUrl(imageUrl));

  // Reset state only when the symbol changes (different base URL).
  // Same-symbol refreshes (only &t= changed) keep the old image visible.
  useEffect(() => {
    const newBase = baseUrl(imageUrl);
    if (newBase !== prevBase.current) {
      setLoading(true);
      setError(false);
      setScale(1);
      setTranslate({ x: 0, y: 0 });
    }
    prevBase.current = newBase;
  }, [imageUrl]);

  const handleDoubleClick = () => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
  };

  if (!imageUrl) {
    return (
      <div className="flex items-center justify-center aspect-[4/3] bg-tg-section-bg rounded-xl text-tg-hint text-sm">
        No chart available
      </div>
    );
  }

  return (
    <div
      className="relative overflow-hidden rounded-xl bg-tg-section-bg touch-manipulation max-h-[75vh]"
      onDoubleClick={handleDoubleClick}
    >
      {loading && <ChartSkeleton />}
      {error && (
        <div className="flex items-center justify-center aspect-[4/3] text-red-400 text-sm">
          Failed to load chart
        </div>
      )}
      <img
        src={imageUrl}
        alt={alt}
        className={`w-full max-h-[75vh] object-contain transition-transform duration-100 ${loading ? 'absolute inset-0 opacity-0 pointer-events-none' : ''}`}
        style={{
          transform: `scale(${scale}) translate(${translate.x}px, ${translate.y}px)`,
          display: error ? 'none' : 'block',
        }}
        onLoad={() => setLoading(false)}
        onError={() => {
          setLoading(false);
          setError(true);
        }}
        draggable={false}
      />
      {scale !== 1 && (
        <button
          onClick={handleDoubleClick}
          className="absolute top-2 right-2 bg-black/50 text-white text-xs px-2 py-1 rounded"
        >
          Reset
        </button>
      )}
    </div>
  );
}
