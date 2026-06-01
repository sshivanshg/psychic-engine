// Thin client for the TradeOS FastAPI backend. Override the base with VITE_API_BASE in web/.env.
const BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000';

async function parseError(r: Response): Promise<string> {
  try {
    return (await r.json())?.detail ?? '';
  } catch {
    return await r.text();
  }
}

async function get(path: string) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error((await parseError(r)) || `${r.status} ${r.statusText}`);
  return r.json();
}

async function send(path: string, method: string, body?: unknown) {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined
  });
  if (!r.ok) throw new Error((await parseError(r)) || `${r.status} ${r.statusText}`);
  return r.json();
}

export interface HoldingInput {
  symbol: string;
  quantity: number;
  avg_cost?: number | null;
  fetch?: boolean;
}

export interface DocUpload {
  symbol: string;
  file: File;
  period?: string;
  filing_date?: string;
  source_url?: string;
}

export const api = {
  health: () => get('/api/health'),
  holdings: () => get('/api/holdings'),
  portfolio: (horizon = 'annual') => get(`/api/portfolio?horizon=${encodeURIComponent(horizon)}`),
  risk: (horizon = 'annual') => get(`/api/risk?horizon=${encodeURIComponent(horizon)}`),
  stock: (sym: string, narrate = false) =>
    get(`/api/stock/${encodeURIComponent(sym)}?narrate=${narrate}`),
  stockSeries: (sym: string, lookback = 400) =>
    get(`/api/stock/${encodeURIComponent(sym)}/series?lookback=${lookback}`),
  briefing: () => get('/api/briefing'),
  docsStatus: () => get('/api/docs/status'),

  // --- evidence layer: every fetched detail per name + portfolio-wide ---
  analyst: (sym: string, horizon = 'annual') =>
    get(`/api/analyst/${encodeURIComponent(sym)}?verdict=false&horizon=${encodeURIComponent(horizon)}`),
  analystVerdict: (sym: string, horizon = 'annual') =>
    get(`/api/analyst/${encodeURIComponent(sym)}?verdict=true&horizon=${encodeURIComponent(horizon)}`),
  analystDeep: (sym: string, horizon = 'annual') =>
    get(`/api/analyst/${encodeURIComponent(sym)}/deep?horizon=${encodeURIComponent(horizon)}`),
  analystHistory: (sym: string, limit = 20) =>
    get(`/api/analyst/${encodeURIComponent(sym)}/history?limit=${limit}`),
  analystRuns: (limit = 40) => get(`/api/analyst/runs?limit=${limit}`),
  stockNews: (sym: string, limit = 60) =>
    get(`/api/stock/${encodeURIComponent(sym)}/news?limit=${limit}`),
  stockDocs: (sym: string) => get(`/api/stock/${encodeURIComponent(sym)}/docs`),
  news: (limit = 200) => get(`/api/news?limit=${limit}`),
  coverage: () => get('/api/coverage'),

  // --- write seam ---
  addHolding: (h: HoldingInput) => send('/api/holdings', 'POST', h),
  removeHolding: (symbol: string) => send(`/api/holdings/${encodeURIComponent(symbol)}`, 'DELETE'),
  ingest: (symbols?: string[]) => send('/api/ingest', 'POST', symbols?.length ? { symbols } : {}),

  async uploadDoc(d: DocUpload) {
    const fd = new FormData();
    fd.append('symbol', d.symbol);
    fd.append('file', d.file);
    if (d.period) fd.append('period', d.period);
    if (d.filing_date) fd.append('filing_date', d.filing_date);
    if (d.source_url) fd.append('source_url', d.source_url);
    const r = await fetch(`${BASE}/api/docs`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await parseError(r)) || `upload failed: ${r.status}`);
    return r.json();
  },

  async ask(symbol: string, question: string) {
    return send('/api/ask', 'POST', { symbol, question });
  },

  // ask the analyst a follow-up over the WHOLE research (facts + deep read + filings + live web)
  async askResearch(symbol: string, question: string, web = true) {
    return send('/api/analyst/ask', 'POST', { symbol, question, web });
  },

  // --- live reasoning monitor (SSE) ---
  streamAnalyzeUrl(opts: { horizon?: string; narrate?: boolean; as_of?: string } = {}) {
    const p = new URLSearchParams();
    p.set('horizon', opts.horizon ?? 'annual');
    p.set('narrate', String(opts.narrate ?? true));
    if (opts.as_of) p.set('as_of', opts.as_of);
    return `${BASE}/api/stream/analyze?${p.toString()}`;
  }
};
