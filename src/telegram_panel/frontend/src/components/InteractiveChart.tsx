import { useEffect, useRef, useCallback } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from 'lightweight-charts';
import type { ChartData } from '../api/types';

interface InteractiveChartProps {
  data: ChartData;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
}

const EMA12_COLOR = '#2196f3';
const SMA26_COLOR = '#ff9800';

const CHART_BG = 'transparent';
const TEXT_COLOR = '#8b8b8b';
const GRID_COLOR = 'rgba(255,255,255,0.04)';

interface SeriesRefs {
  candleSeries?: ISeriesApi<'Candlestick'>;
  volumeSeries?: ISeriesApi<'Histogram'>;
  ema12Series?: ISeriesApi<'Line'>;
  sma26Series?: ISeriesApi<'Line'>;
  rsiSeries?: ISeriesApi<'Line'>;
  macdSeries?: ISeriesApi<'Line'>;
  signalSeries?: ISeriesApi<'Line'>;
  histSeries?: ISeriesApi<'Histogram'>;
  zeroSeries?: ISeriesApi<'Line'>;
}

interface ViewportState {
  visibleRange: { from: Time; to: Time } | null;
  scrollPosition: number;
}

export function InteractiveChart({ data, fullscreen, onToggleFullscreen }: InteractiveChartProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRefs = useRef<SeriesRefs>({});
  const lastDataRef = useRef<ChartData | null>(null);
  const viewportRef = useRef<ViewportState | null>(null);
  const isFirstRender = useRef(true);

  // Функция для сохранения viewport
  const saveViewport = useCallback(() => {
    if (!chartRef.current) return null;
    const timeScale = chartRef.current.timeScale();
    return {
      visibleRange: timeScale.getVisibleRange(),
      scrollPosition: timeScale.scrollPosition(),
    };
  }, []);

  // Функция для восстановления viewport
  const restoreViewport = useCallback((viewport: ViewportState | null) => {
    if (!chartRef.current || !viewport) return;
    const timeScale = chartRef.current.timeScale();
    if (viewport.visibleRange) {
      try {
        timeScale.setVisibleRange(viewport.visibleRange as any);
      } catch {
        // Игнорируем ошибки, если диапазон уже невалиден
      }
    }
  }, []);

  // Функция для инкрементального обновления данных
  const updateSeriesData = useCallback((newData: ChartData, series: SeriesRefs) => {
    if (!chartRef.current) return;

    try {

    const newCandles = newData.candles;
    const oldCandles = lastDataRef.current?.candles || [];

    // Находим новые свечи (которых нет в старых данных)
    const oldTimes = new Set(oldCandles.map(c => c.time));
    const newCandlesOnly = newCandles.filter(c => !oldTimes.has(c.time));

    // Находим обновленные свечи (существующие, но с изменившимися значениями)
    const oldCandleMap = new Map(oldCandles.map(c => [c.time, c]));
    const updatedCandles = newCandles.filter(c => {
      const oldC = oldCandleMap.get(c.time);
      if (!oldC) return false;
      return oldC.high !== c.high || oldC.low !== c.low || oldC.close !== c.close || oldC.open !== c.open;
    });

    // Обновляем новые свечи
    if (series.candleSeries && newCandlesOnly.length > 0) {
      for (const c of newCandlesOnly) {
        series.candleSeries.update({
          time: c.time as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        });
      }
    }

    // Обновляем изменившиеся свечи
    if (series.candleSeries && updatedCandles.length > 0) {
      for (const c of updatedCandles) {
        series.candleSeries.update({
          time: c.time as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        });
      }
    }

    // Обновляем объемы для новых свечей
    if (series.volumeSeries && newCandlesOnly.length > 0) {
      for (const c of newCandlesOnly) {
        series.volumeSeries.update({
          time: c.time as Time,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(38,166,154,0.25)' : 'rgba(239,83,80,0.25)',
        });
      }
    }

    // Обновляем объемы для изменившихся свечей
    if (series.volumeSeries && updatedCandles.length > 0) {
      for (const c of updatedCandles) {
        series.volumeSeries.update({
          time: c.time as Time,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(38,166,154,0.25)' : 'rgba(239,83,80,0.25)',
        });
      }
    }

    // Обновляем EMA12
    const newEma12 = newData.indicators.ema12 || [];
    const oldEma12 = lastDataRef.current?.indicators.ema12 || [];
    if (series.ema12Series) {
      const oldEma12Times = new Set(oldEma12.map(e => e.time));
      for (const p of newEma12.filter(e => !oldEma12Times.has(e.time))) {
        series.ema12Series.update({ time: p.time as Time, value: p.value });
      }
    }

    // Обновляем SMA26
    const newSma26 = newData.indicators.sma26 || [];
    const oldSma26 = lastDataRef.current?.indicators.sma26 || [];
    if (series.sma26Series) {
      const oldSma26Times = new Set(oldSma26.map(s => s.time));
      for (const p of newSma26.filter(s => !oldSma26Times.has(s.time))) {
        series.sma26Series.update({ time: p.time as Time, value: p.value });
      }
    }

    // Обновляем RSI
    const newRsi = newData.indicators.rsi || [];
    const oldRsi = lastDataRef.current?.indicators.rsi || [];
    if (series.rsiSeries) {
      const oldRsiTimes = new Set(oldRsi.map(r => r.time));
      for (const p of newRsi.filter(r => !oldRsiTimes.has(r.time))) {
        series.rsiSeries.update({ time: p.time as Time, value: p.value });
      }
    }

    // Обновляем MACD
    const newMacd = newData.indicators.macd || [];
    const oldMacd = lastDataRef.current?.indicators.macd || [];
    if (series.macdSeries) {
      const oldMacdTimes = new Set(oldMacd.map(m => m.time));
      for (const p of newMacd.filter(m => !oldMacdTimes.has(m.time))) {
        series.macdSeries.update({ time: p.time as Time, value: p.value });
      }
    }

    // Обновляем MACD Signal
    const newSignal = newData.indicators.macd_signal || [];
    const oldSignal = lastDataRef.current?.indicators.macd_signal || [];
    if (series.signalSeries) {
      const oldSignalTimes = new Set(oldSignal.map(s => s.time));
      for (const p of newSignal.filter(s => !oldSignalTimes.has(s.time))) {
        series.signalSeries.update({ time: p.time as Time, value: p.value });
      }
    }

    // Обновляем MACD Histogram
    const newHist = newData.indicators.macd_histogram || [];
    const oldHist = lastDataRef.current?.indicators.macd_histogram || [];
    if (series.histSeries) {
      const oldHistTimes = new Set(oldHist.map(h => h.time));
      for (const p of newHist.filter(h => !oldHistTimes.has(h.time))) {
        series.histSeries.update({
          time: p.time as Time,
          value: p.value,
          color: p.value >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
        });
      }
    }
    } catch (error) {
      // При ошибке инкрементального обновления - пересоздаем график
      console.warn('Failed to update chart incrementally, recreating:', error);
      createFullChart(newData);
    }
  }, []);

  // Функция для полного создания графика
  const createFullChart = useCallback((chartData: ChartData) => {
    if (!containerRef.current || chartData.candles.length === 0) return;

    // Очищаем предыдущий график
    if (chartRef.current) {
      viewportRef.current = saveViewport();
      chartRef.current.remove();
      chartRef.current = null;
      seriesRefs.current = {};
    }

    // Создаем новый график
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid as const, color: CHART_BG },
        textColor: TEXT_COLOR,
      },
      grid: {
        vertLines: { color: GRID_COLOR },
        horzLines: { color: GRID_COLOR },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.0, bottom: 0.4 },
      },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Magnet },
      handleScroll: { vertTouchDrag: false },
      height: 480,
    });
    chartRef.current = chart;
    const series: SeriesRefs = {};
    seriesRefs.current = series;

    // ===== PRICE ZONE (top 60%) =====
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderUpColor: '#26a69a',
      borderDownColor: '#ef5350',
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });
    candleSeries.setData(
      chartData.candles.map((c) => ({
        time: c.time as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );
    series.candleSeries = candleSeries;

    // Volume
    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.48, bottom: 0.4 },
    });
    volumeSeries.setData(
      chartData.candles.map((c) => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(38,166,154,0.25)' : 'rgba(239,83,80,0.25)',
      }))
    );
    series.volumeSeries = volumeSeries;

    // EMA 12
    const ema12Data = chartData.indicators.ema12 || [];
    if (ema12Data.length > 0) {
      const ema12Series = chart.addLineSeries({
        color: EMA12_COLOR,
        lineWidth: 1,
        title: 'EMA 12',
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ema12Series.setData(ema12Data.map((p) => ({ time: p.time as Time, value: p.value })));
      series.ema12Series = ema12Series;
    }

    // SMA 26
    const sma26Data = chartData.indicators.sma26 || [];
    if (sma26Data.length > 0) {
      const sma26Series = chart.addLineSeries({
        color: SMA26_COLOR,
        lineWidth: 1,
        title: 'SMA 26',
        priceLineVisible: false,
        lastValueVisible: false,
      });
      sma26Series.setData(sma26Data.map((p) => ({ time: p.time as Time, value: p.value })));
      series.sma26Series = sma26Series;
    }

    // Position markers
    if (chartData.position && chartData.position.entry_price > 0) {
      const pos = chartData.position;
      const posColor = pos.side === 'LONG' ? '#00e676' : '#ff1744';

      candleSeries.createPriceLine({
        price: pos.entry_price,
        color: posColor,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${pos.side} @ ${pos.entry_price}`,
      });
      if (pos.sl > 0) {
        candleSeries.createPriceLine({
          price: pos.sl,
          color: '#ef5350',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: `SL: ${pos.sl}`,
        });
      }
      if (pos.tp > 0) {
        candleSeries.createPriceLine({
          price: pos.tp,
          color: '#26a69a',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: `TP: ${pos.tp}`,
        });
      }
    }

    // ===== RSI ZONE (middle ~18%) =====
    const rsiData = chartData.indicators.rsi || [];
    if (rsiData.length > 0) {
      const rsiSeries = chart.addLineSeries({
        color: '#2ca02c',
        lineWidth: 1,
        title: 'RSI',
        priceScaleId: 'rsi',
        priceLineVisible: false,
        lastValueVisible: true,
      });
      chart.priceScale('rsi').applyOptions({
        scaleMargins: { top: 0.62, bottom: 0.22 },
        borderVisible: false,
      });
      rsiSeries.setData(rsiData.map((p) => ({ time: p.time as Time, value: p.value })));

      rsiSeries.createPriceLine({
        price: 70,
        color: 'rgba(239,83,80,0.4)',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: '',
      });
      rsiSeries.createPriceLine({
        price: 30,
        color: 'rgba(38,166,154,0.4)',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: false,
        title: '',
      });
      series.rsiSeries = rsiSeries;
    }

    // ===== MACD ZONE (bottom ~18%) =====
    const macdData = chartData.indicators.macd || [];
    const signalData = chartData.indicators.macd_signal || [];
    const histData = chartData.indicators.macd_histogram || [];

    if (macdData.length > 0) {
      // Histogram
      if (histData.length > 0) {
        const histSeries = chart.addHistogramSeries({
          priceScaleId: 'macd',
          priceLineVisible: false,
          lastValueVisible: false,
          title: '',
        });
        histSeries.setData(
          histData.map((p) => ({
            time: p.time as Time,
            value: p.value,
            color: p.value >= 0 ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
          }))
        );
        series.histSeries = histSeries;
      }

      // MACD line
      const macdSeries = chart.addLineSeries({
        color: '#2196f3',
        lineWidth: 1,
        title: 'MACD',
        priceScaleId: 'macd',
        priceLineVisible: false,
        lastValueVisible: true,
      });
      chart.priceScale('macd').applyOptions({
        scaleMargins: { top: 0.82, bottom: 0.02 },
        borderVisible: false,
      });
      macdSeries.setData(macdData.map((p) => ({ time: p.time as Time, value: p.value })));
      series.macdSeries = macdSeries;

      // Signal line
      if (signalData.length > 0) {
        const signalSeries = chart.addLineSeries({
          color: '#ff9800',
          lineWidth: 1,
          title: 'Signal',
          priceScaleId: 'macd',
          priceLineVisible: false,
          lastValueVisible: true,
        });
        signalSeries.setData(signalData.map((p) => ({ time: p.time as Time, value: p.value })));
        series.signalSeries = signalSeries;
      }

      // Zero line
      if (macdData.length >= 2) {
        const zeroSeries = chart.addLineSeries({
          color: 'rgba(255,255,255,0.1)',
          lineWidth: 1,
          priceScaleId: 'macd',
          priceLineVisible: false,
          lastValueVisible: false,
          title: '',
        });
        zeroSeries.setData([
          { time: macdData[0].time as Time, value: 0 },
          { time: macdData[macdData.length - 1].time as Time, value: 0 },
        ]);
        series.zeroSeries = zeroSeries;
      }
    }

    // ResizeObserver на containerRef — он имеет w-full h-full внутри absolute inset-0,
    // поэтому получает реальные px-размеры после layout
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          chart.resize(width, height);
        }
      }
    });
    if (containerRef.current) resizeObserver.observe(containerRef.current);

    // Сохраняем cleanup функцию
    chartRef.current = chart;

    return () => {
      resizeObserver.disconnect();
    };
  }, [fullscreen, saveViewport]);

  const lastFullscreenRef = useRef(fullscreen);

  useEffect(() => {
    if (!containerRef.current || data.candles.length === 0) return;

    const lastData = lastDataRef.current;
    const fullscreenChanged = lastFullscreenRef.current !== fullscreen;
    lastFullscreenRef.current = fullscreen;

    // Пересоздаём график: первый рендер, смена символа/диапазона, или смена fullscreen
    if (isFirstRender.current || !lastData || lastData.symbol !== data.symbol || lastData.range !== data.range || fullscreenChanged) {
      isFirstRender.current = false;
      lastDataRef.current = data;

      if (fullscreenChanged) {
        // DOM ещё не пересчитал layout с новыми классами — ждём 2 фрейма
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            createFullChart(data);
            chartRef.current?.timeScale().fitContent();
          });
        });
      } else {
        createFullChart(data);
        if (viewportRef.current) {
          restoreViewport(viewportRef.current);
          viewportRef.current = null;
        } else {
          chartRef.current?.timeScale().fitContent();
        }
      }
      return;
    }

    // Инкрементальное обновление при тех же данных
    if (lastData && lastData.symbol === data.symbol && lastData.range === data.range) {
      const savedViewport = saveViewport();
      updateSeriesData(data, seriesRefs.current);
      restoreViewport(savedViewport);
    } else {
      createFullChart(data);
    }

    lastDataRef.current = data;
  }, [data, fullscreen, createFullChart, updateSeriesData, saveViewport, restoreViewport]);

  // Cleanup при размонтировании
  useEffect(() => {
    return () => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []);

  return (
    <div ref={wrapperRef} className={`w-full ${fullscreen ? 'absolute inset-0' : 'relative'}`}>
      {/* Fullscreen toggle */}
      <button
        onClick={onToggleFullscreen}
        className="absolute top-1 right-1 z-10 bg-black/50 text-white text-[10px] px-2 py-1 rounded hover:bg-black/70 transition-colors"
      >
        {fullscreen ? 'Exit' : 'Fullscreen'}
      </button>

      <div ref={containerRef} className={fullscreen ? 'w-full h-full' : 'w-full'} />
    </div>
  );
}
