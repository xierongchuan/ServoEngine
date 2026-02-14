import { useEffect, useState, useCallback } from 'react';
import { getChartsList, getChartUrl } from '../api/client';
import type { ChartFile } from '../api/types';
import { ChartViewer } from '../components/ChartViewer';
import { Spinner } from '../components/Spinner';

export function Charts({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [charts, setCharts] = useState<ChartFile[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);

  const fetchCharts = useCallback(async () => {
    try {
      const list = await getChartsList();
      setCharts(list);
      if (list.length > 0 && !selected) {
        setSelected(list[0].filename);
      }
    } catch (err) {
      console.error('Charts fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    fetchCharts();
  }, [fetchCharts]);

  useEffect(() => {
    return subscribe('chart_update', () => {
      setRefreshKey((k) => k + 1);
      fetchCharts();
    });
  }, [subscribe, fetchCharts]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  const selectedChart = charts.find((c) => c.filename === selected);

  return (
    <div className="flex flex-col gap-4 p-4">
      <span className="text-lg font-semibold text-tg-text">Charts</span>

      {charts.length === 0 ? (
        <div className="text-center py-12 text-tg-hint text-sm">No charts available</div>
      ) : (
        <>
          {/* Symbol selector */}
          <div className="flex flex-wrap gap-1.5">
            {charts.map((chart) => (
              <button
                key={chart.filename}
                onClick={() => setSelected(chart.filename)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                  selected === chart.filename
                    ? 'bg-tg-button text-white'
                    : 'bg-tg-section-bg text-tg-hint'
                }`}
              >
                {chart.filename.replace('.png', '')}
              </button>
            ))}
          </div>

          {/* Chart display */}
          {selected && (
            <>
              <ChartViewer
                imageUrl={`${getChartUrl(selected)}&t=${refreshKey}`}
                alt={selected}
              />
              {selectedChart && (
                <div className="text-xs text-tg-hint text-center">
                  Last updated: {new Date(selectedChart.modified * 1000).toLocaleString()}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
