// Thin client for the TradeOS FastAPI backend. Override the base with VITE_API_BASE in web/.env.
const BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000';

async function get(path: string) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) {
    let detail = '';
    try {
      detail = (await r.json())?.detail ?? '';
    } catch {
      detail = await r.text();
    }
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
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
  evalSignals: (horizon = 21) => get(`/api/eval?horizon=${horizon}`),
  briefing: () => get('/api/briefing'),
  async ask(symbol: string, question: string) {
    const r = await fetch(`${BASE}/api/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, question })
    });
    if (!r.ok) throw new Error(`ask failed: ${r.status}`);
    return r.json();
  }
};
