// ECharts option builders + a theme-aware palette. Each function takes API JSON and returns an
// ECharts `option`. Colours are pulled LIVE from the app's CSS custom properties (set per theme on
// <html data-theme> — see app.css), so charts follow the black/light toggle. Pages wrap <Chart> in
// {#key $theme} so these builders re-run and re-read the variables when the theme flips.

function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function getPalette() {
  return {
    text: cssVar('--text', '#e6e9ef'),
    muted: cssVar('--muted', '#7c8698'),
    border: cssVar('--border', '#232a36'),
    grid: cssVar('--border-soft', '#1b2230'),
    panel: cssVar('--panel', '#131722'),
    bg: cssVar('--bg', '#0b0e14'),
    accent: cssVar('--accent', '#4c8dff'),
    good: cssVar('--good', '#26a69a'),
    bad: cssVar('--bad', '#ef5350'),
    warn: cssVar('--warn', '#f0a020'),
    purple: cssVar('--purple', '#a78bfa'),
    cyan: '#22d3ee',
    series: ['#4c8dff', '#26a69a', '#f0a020', '#a78bfa', '#ef5350', '#22d3ee', '#f472b6', '#94a3b8']
  };
}

type Palette = ReturnType<typeof getPalette>;

const FONT = 'ui-sans-serif, -apple-system, system-ui, sans-serif';

// Shared axis/grid/tooltip styling derived from the current palette.
function base(p: Palette) {
  return {
    tooltip: {
      backgroundColor: p.panel,
      borderColor: p.border,
      textStyle: { color: p.text, fontFamily: FONT, fontSize: 12 }
    },
    axisLabel: { color: p.muted, fontFamily: FONT, fontSize: 11 },
    axisLine: { lineStyle: { color: p.border } },
    splitLine: { lineStyle: { color: p.grid } },
    baseGrid: { left: 48, right: 18, top: 28, bottom: 30 }
  };
}

// --- per-stock price chart: close + SMA overlays ------------------------------------------
export function priceChartOption(s: any) {
  const p = getPalette();
  const { tooltip, axisLabel, axisLine, splitLine, baseGrid } = base(p);
  const line = (name: string, data: any[], color: string, width = 2, dashed = false) => ({
    name, type: 'line', data, showSymbol: false, smooth: false,
    lineStyle: { color, width, type: dashed ? 'dashed' : 'solid' },
    emphasis: { focus: 'series' }
  });
  return {
    color: p.series,
    tooltip: { trigger: 'axis', ...tooltip },
    legend: { top: 0, textStyle: { color: p.muted, fontFamily: FONT, fontSize: 11 },
              data: ['Close', 'SMA20', 'SMA50', 'SMA200'] },
    grid: { ...baseGrid, top: 34, bottom: 56 },
    xAxis: { type: 'category', data: s.dates, boundaryGap: false, axisLabel,
             axisLine, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel, splitLine, axisLine: { show: false } },
    dataZoom: [
      { type: 'inside', start: 55, end: 100 },
      { type: 'slider', start: 55, end: 100, height: 18, bottom: 14,
        borderColor: p.border, fillerColor: 'rgba(76,141,255,0.12)',
        handleStyle: { color: p.accent }, textStyle: { color: p.muted } }
    ],
    series: [
      { ...line('Close', s.close, p.accent, 2),
        areaStyle: { color: 'rgba(76,141,255,0.08)' } },
      line('SMA20', s.sma20, p.warn, 1.3),
      line('SMA50', s.sma50, p.cyan, 1.3),
      line('SMA200', s.sma200, p.purple, 1.3, true)
    ]
  };
}

// --- portfolio risk: %CTR vs weight, horizontal bars --------------------------------------
export function riskContribOption(positions: any[]) {
  const p = getPalette();
  const { tooltip, axisLabel, axisLine, splitLine, baseGrid } = base(p);
  const rows = positions
    .filter((q) => q.risk_contribution_pct != null)
    .sort((a, b) => a.risk_contribution_pct - b.risk_contribution_pct);
  const names = rows.map((q) => q.symbol.replace('.NS', ''));
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltip,
               valueFormatter: (v: number) => `${v?.toFixed(1)}%` },
    legend: { top: 0, textStyle: { color: p.muted, fontSize: 11 }, data: ['Risk %', 'Weight %'] },
    grid: { ...baseGrid, left: 76, top: 30 },
    xAxis: { type: 'value', axisLabel: { ...axisLabel, formatter: '{value}%' }, splitLine, axisLine: { show: false } },
    yAxis: { type: 'category', data: names, axisLabel, axisLine, axisTick: { show: false } },
    series: [
      { name: 'Risk %', type: 'bar', data: rows.map((q) => q.risk_contribution_pct),
        itemStyle: { color: p.bad, borderRadius: [0, 3, 3, 0] }, barWidth: 11 },
      { name: 'Weight %', type: 'bar', data: rows.map((q) => q.weight_pct),
        itemStyle: { color: p.accent, borderRadius: [0, 3, 3, 0] }, barWidth: 11 }
    ]
  };
}

// --- sector allocation donut --------------------------------------------------------------
export function sectorDonutOption(sectors: any[]) {
  const p = getPalette();
  const { tooltip } = base(p);
  return {
    color: p.series,
    tooltip: { trigger: 'item', ...tooltip, valueFormatter: (v: number) => `${v?.toFixed(1)}%` },
    legend: { type: 'scroll', orient: 'vertical', right: 0, top: 'middle',
              textStyle: { color: p.muted, fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['52%', '78%'], center: ['38%', '52%'], avoidLabelOverlap: true,
      itemStyle: { borderColor: p.panel, borderWidth: 2 },
      label: { show: false }, labelLine: { show: false },
      data: sectors.map((s) => ({ name: s.sector, value: s.weight_pct }))
    }]
  };
}

// --- correlation heatmap ------------------------------------------------------------------
export function corrHeatmapOption(corr: Record<string, Record<string, number>>) {
  const p = getPalette();
  const { tooltip, axisLabel, axisLine } = base(p);
  const syms = Object.keys(corr);
  const short = syms.map((s) => s.replace('.NS', ''));
  const data: [number, number, number][] = [];
  syms.forEach((a, i) => syms.forEach((b, j) => data.push([j, i, corr[a]?.[b] ?? 0])));
  return {
    tooltip: { ...tooltip, formatter: (pt: any) => `${short[pt.data[1]]} · ${short[pt.data[0]]}<br/>ρ = ${pt.data[2]}` },
    grid: { left: 64, right: 16, top: 16, bottom: 48 },
    xAxis: { type: 'category', data: short, axisLabel: { ...axisLabel, rotate: 45 }, axisLine, splitArea: { show: true } },
    yAxis: { type: 'category', data: short, axisLabel, axisLine, splitArea: { show: true } },
    visualMap: {
      min: -1, max: 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
      inRange: { color: [p.good, p.grid, p.bad] },
      textStyle: { color: p.muted, fontSize: 10 }, itemWidth: 12, itemHeight: 90
    },
    series: [{
      type: 'heatmap', data,
      label: { show: true, color: p.text, fontSize: 10, formatter: (pt: any) => pt.data[2].toFixed(2) },
      itemStyle: { borderColor: p.panel, borderWidth: 2 }
    }]
  };
}

// --- gauges -------------------------------------------------------------------------------
export function gaugeOption(value: number | null, label: string, zones: [number, string][]) {
  const p = getPalette();
  return {
    series: [{
      type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '94%', center: ['50%', '58%'],
      progress: { show: false },
      axisLine: { lineStyle: { width: 12, color: zones } },
      pointer: { width: 4, length: '62%', itemStyle: { color: p.text } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      anchor: { show: true, size: 8, itemStyle: { color: p.text } },
      detail: { valueAnimation: true, fontSize: 26, fontWeight: 700, color: p.text,
                offsetCenter: [0, '38%'], formatter: (v: number) => (value == null ? '—' : Math.round(v).toString()) },
      title: { offsetCenter: [0, '74%'], color: p.muted, fontSize: 11 },
      data: [{ value: value ?? 0, name: label }]
    }]
  };
}

export const attentionGauge = (score: number | null) => {
  const p = getPalette();
  return gaugeOption(score, 'attention', [[0.4, p.good], [0.66, p.warn], [1, p.bad]]);
};

export const rsiGauge = (rsi: number | null) => {
  const p = getPalette();
  return gaugeOption(rsi, 'RSI(14)', [[0.3, p.good], [0.7, p.muted], [1, p.bad]]);
};

// --- 1m/3m returns bars -------------------------------------------------------------------
export function returnsBarOption(card: any) {
  const p = getPalette();
  const { tooltip, axisLabel, splitLine, baseGrid } = base(p);
  const t = card.technical ?? {};
  const items = [
    { k: '1m', v: t.ret_1m_pct }, { k: '3m', v: t.ret_3m_pct }, { k: 'vs 200SMA', v: t.price_vs_sma200_pct }
  ].filter((x) => x.v != null);
  return {
    tooltip: { ...tooltip, valueFormatter: (v: number) => `${v?.toFixed(1)}%` },
    grid: { ...baseGrid, left: 64 },
    xAxis: { type: 'value', axisLabel: { ...axisLabel, formatter: '{value}%' }, splitLine,
             axisLine: { show: false } },
    yAxis: { type: 'category', data: items.map((x) => x.k), axisLabel, axisLine: { lineStyle: { color: p.border } }, axisTick: { show: false } },
    series: [{
      type: 'bar', data: items.map((x) => ({ value: x.v,
        itemStyle: { color: x.v >= 0 ? p.good : p.bad, borderRadius: 3 } })),
      barWidth: 16
    }]
  };
}

// --- signal eval: IC per signal, coloured by significance ---------------------------------
export function icBarOption(signals: Record<string, any>) {
  const p = getPalette();
  const { tooltip, axisLabel, axisLine, splitLine, baseGrid } = base(p);
  const rows = Object.entries(signals)
    .map(([name, s]) => ({ name, ic: s.ic, t: s.t_stat, kind: s.kind }))
    .filter((r) => r.ic != null);
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltip,
               formatter: (ps: any) => {
                 const r = rows[ps[0].dataIndex];
                 return `${r.name} (${r.kind})<br/>IC ${r.ic}<br/>t ${r.t ?? '—'}`;
               } },
    grid: { ...baseGrid, left: 110 },
    xAxis: { type: 'value', axisLabel, splitLine, axisLine: { show: false } },
    yAxis: { type: 'category', data: rows.map((r) => r.name), axisLabel, axisLine, axisTick: { show: false } },
    series: [{
      type: 'bar', barWidth: 13,
      data: rows.map((r) => ({ value: r.ic,
        itemStyle: { color: Math.abs(r.t ?? 0) >= 2 ? p.warn : p.accent, borderRadius: 3 } }))
    }]
  };
}
