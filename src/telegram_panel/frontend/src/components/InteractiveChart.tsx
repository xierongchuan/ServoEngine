import { useEffect, useRef } from 'react';
import {
  createChart, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type ISeriesApi, type IPriceLine, type Time,
} from 'lightweight-charts';
import type { ChartData } from '../api/types';

interface InteractiveChartProps {
  data: ChartData;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}

interface SeriesRefs {
  candles: ISeriesApi<'Candlestick'>;
  volume: ISeriesApi<'Histogram'>;
  lines: Record<string, ISeriesApi<'Line'>>;
  histogram: ISeriesApi<'Histogram'>;
  positionLines: IPriceLine[];
}

export function InteractiveChart({ data, fullscreen, onToggleFullscreen }: InteractiveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<SeriesRefs | null>(null);
  const dataKeyRef = useRef('');
  const latestTimeRef = useRef<number | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8b8b8b' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0, bottom: 0.4 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Magnet },
      handleScroll: { vertTouchDrag: false },
      height: 480,
    });
    chartRef.current = chart;
    const candles = chart.addCandlestickSeries({
      upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a',
      borderDownColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume' });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.48, bottom: 0.4 } });
    const lines: Record<string, ISeriesApi<'Line'>> = {};
    // Серии создаются даже до warm-up: новые индикаторы появляются без пересоздания графика.
    for (const [name, title, color, scale] of [
      ['ema12', 'EMA 12', '#2196f3', 'right'], ['sma26', 'SMA 26', '#ff9800', 'right'],
      ['rsi', 'RSI', '#2ca02c', 'rsi'], ['macd', 'MACD', '#2196f3', 'macd'],
      ['macd_signal', 'Signal', '#ff9800', 'macd'],
    ]) {
      lines[name] = chart.addLineSeries({ color, title, lineWidth: 1, priceScaleId: scale,
        priceLineVisible: false, lastValueVisible: scale !== 'right' });
    }
    chart.priceScale('rsi').applyOptions({ scaleMargins: { top: 0.62, bottom: 0.22 }, borderVisible: false });
    chart.priceScale('macd').applyOptions({ scaleMargins: { top: 0.82, bottom: 0.02 }, borderVisible: false });
    for (const price of [30, 70]) lines.rsi.createPriceLine({ price,
      color: price === 70 ? 'rgba(239,83,80,0.4)' : 'rgba(38,166,154,0.4)',
      lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
    lines.macd.createPriceLine({ price: 0, color: 'rgba(255,255,255,0.1)',
      lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
    const histogram = chart.addHistogramSeries({ priceScaleId: 'macd', priceLineVisible: false, lastValueVisible: false });
    seriesRef.current = { candles, volume, lines, histogram, positionLines: [] };
    return () => {
      seriesRef.current = null;
      chartRef.current = null;
      dataKeyRef.current = '';
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    const chart = chartRef.current;
    if (!container || !chart) return;
    const resize = () => chart.resize(container.clientWidth, fullscreen ? Math.max(container.clientHeight, 200) : 480);
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [fullscreen]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;
    const key = `${data.symbol}:${data.interval}:${data.range}`;
    const sameKey = dataKeyRef.current === key;
    const visibleRange = sameKey ? chart.timeScale().getVisibleRange() : null;
    const followingLatest = sameKey && visibleRange !== null && latestTimeRef.current !== null
      && Number(visibleRange.to) >= latestTimeRef.current;
    // Ответ API — полный снимок, включая правки истории, объёма и незакрытой свечи.
    series.candles.setData(data.candles.map(c => ({ ...c, time: c.time as Time })));
    series.volume.setData(data.candles.map(c => ({ time: c.time as Time, value: c.volume,
      color: c.close >= c.open ? 'rgba(38,166,154,0.25)' : 'rgba(239,83,80,0.25)' })));
    for (const [name, line] of Object.entries(series.lines)) {
      line.setData((data.indicators[name] || []).map(p => ({ time: p.time as Time, value: p.value })));
    }
    series.histogram.setData((data.indicators.macd_histogram || []).map(p => ({ time: p.time as Time,
      value: p.value, color: p.value >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)' })));
    series.positionLines.forEach(line => series.candles.removePriceLine(line));
    series.positionLines = [];
    const position = data.position;
    if (position) {
      for (const [price, title, color] of [
        [position.entry_price, `${position.side} @ ${position.entry_price}`, position.side === 'LONG' ? '#00e676' : '#ff1744'],
        [position.sl, `SL: ${position.sl}`, '#ef5350'], [position.tp, `TP: ${position.tp}`, '#26a69a'],
      ] as [number, string, string][]) {
        if (Number.isFinite(price) && price > 0) series.positionLines.push(series.candles.createPriceLine({
          price, title, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true,
        }));
      }
    }
    if (!sameKey) chart.timeScale().fitContent();
    else if (visibleRange && data.candles.length) {
      chart.timeScale().setVisibleRange(visibleRange);
      if (followingLatest) chart.timeScale().scrollToRealTime();
    }
    dataKeyRef.current = key;
    latestTimeRef.current = data.candles[data.candles.length - 1]?.time ?? null;
  }, [data]);

  return (
    <div className={`w-full ${fullscreen ? 'absolute inset-0' : 'relative'}`}>
      <div className="absolute top-1 right-1 z-10 flex gap-2">
        <button onClick={() => chartRef.current?.timeScale().fitContent()}
          className="min-h-11 bg-black/50 text-white text-xs px-2 py-1 rounded hover:bg-black/70 transition-colors">
          Вся история
        </button>
        <button onClick={onToggleFullscreen} aria-pressed={fullscreen}
          className="min-h-11 bg-black/50 text-white text-xs px-2 py-1 rounded hover:bg-black/70 transition-colors">
          {fullscreen ? 'Свернуть' : 'На весь экран'}
        </button>
      </div>
      <div ref={containerRef} role="img" aria-label={`График ${data.symbol}, интервал ${data.interval}, ${data.candles.length} свечей. EMA 12, SMA 26, RSI и MACD.`}
        className={fullscreen ? 'w-full h-full' : 'w-full h-[480px]'} />
    </div>
  );
}
