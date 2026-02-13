import { useEffect, useRef } from 'react';

interface LogViewerProps {
  lines: string[];
  autoScroll?: boolean;
}

function getLineColor(line: string): string {
  if (line.includes('ERROR') || line.includes('error') || line.includes('❌')) return 'text-red-400';
  if (line.includes('WARNING') || line.includes('warning') || line.includes('⚠️')) return 'text-amber-400';
  if (line.includes('SUCCESS') || line.includes('✅')) return 'text-green-400';
  return 'text-tg-text/80';
}

export function LogViewer({ lines, autoScroll = true }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  if (lines.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-tg-hint text-sm">
        No logs available
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="overflow-y-auto font-mono text-xs leading-5 bg-black/30 rounded-lg p-3 max-h-[calc(100vh-220px)]"
    >
      {lines.slice(-500).map((line, i) => (
        <div key={i} className={`whitespace-pre-wrap break-all ${getLineColor(line)}`}>
          <span className="text-tg-hint/50 select-none mr-2 inline-block w-8 text-right">
            {lines.length - 500 + i + 1 > 0 ? i + 1 : i + 1}
          </span>
          {line}
        </div>
      ))}
    </div>
  );
}
