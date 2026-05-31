// Display helpers — keep "missing data → —" honest everywhere (never a fabricated 0).
export const num = (v: unknown, d = 2): string =>
  v == null || v === '' ? '—' : Number(v).toFixed(d);

export const pct = (v: unknown, d = 1): string =>
  v == null || v === '' ? '—' : `${Number(v).toFixed(d)}%`;

export const signed = (v: unknown, d = 1): string => {
  if (v == null || v === '') return '—';
  const n = Number(v);
  return `${n >= 0 ? '+' : ''}${n.toFixed(d)}`;
};

// Attention 0–100 → a warmth class (calm → hot). Descriptive, not a buy/sell colour.
export const attnClass = (score: unknown): string => {
  if (score == null) return 'attn-none';
  const s = Number(score);
  if (s >= 66) return 'attn-hot';
  if (s >= 40) return 'attn-warm';
  return 'attn-calm';
};

export const dialClass = (v: string | null | undefined): string => {
  const bad = ['downtrend', 'declining', 'contracting', 'overbought', 'oversold', 'negative', 'near lows', 'high'];
  const good = ['uptrend', 'strong', 'growing', 'expanding', 'positive', 'at highs'];
  if (v && bad.includes(v)) return 'dial-bad';
  if (v && good.includes(v)) return 'dial-good';
  return 'dial-neutral';
};
