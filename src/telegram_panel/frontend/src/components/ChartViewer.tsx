import { useState } from 'react';
import { Spinner } from './Spinner';

interface ChartViewerProps {
  imageUrl: string;
  alt?: string;
}

export function ChartViewer({ imageUrl, alt = 'Chart' }: ChartViewerProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });

  const handleDoubleClick = () => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
  };

  if (!imageUrl) {
    return (
      <div className="flex items-center justify-center h-64 bg-tg-section-bg rounded-xl text-tg-hint text-sm">
        No chart available
      </div>
    );
  }

  return (
    <div
      className="relative overflow-hidden rounded-xl bg-tg-section-bg touch-manipulation"
      onDoubleClick={handleDoubleClick}
    >
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Spinner size={32} />
        </div>
      )}
      {error && (
        <div className="flex items-center justify-center h-64 text-red-400 text-sm">
          Failed to load chart
        </div>
      )}
      <img
        src={imageUrl}
        alt={alt}
        className="w-full transition-transform duration-100"
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
