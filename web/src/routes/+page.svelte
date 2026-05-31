<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { num, pct, attnClass, dialClass } from '$lib/format';
  import Chart from '$lib/Chart.svelte';
  import { riskContribOption, sectorDonutOption, corrHeatmapOption } from '$lib/charts';

  let p = $state<any>(null); // portfolio (cards + overviews)
  let rk = $state<any>(null); // risk (positions + correlation)
  let error = $state<string | null>(null);
  let loading = $state(true);

  onMount(async () => {
    try {
      [p, rk] = await Promise.all([api.portfolio(), api.risk()]);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });
</script>

<div class="topbar">
  <div>
    <h1>Portfolio</h1>
    {#if p}<div class="sub">as of {p.as_of} · {p.cards.length} holdings · horizon {p.horizon}</div>{/if}
  </div>
</div>

{#if loading}
  <div class="loading">Loading the book…</div>
{:else if error}
  <div class="err">
    {error}
    <div class="note" style="margin-top:0.5rem">Is the API up? <code>uv run tradeos serve</code> · then <code>tradeos ingest</code>.</div>
  </div>
{:else if p}
  {@const r = p.risk_overview}
  {@const s = p.sector_overview}
  <div class="kpis">
    <div class="kpi"><div class="k">Book vol (ann.)</div><div class="v">{pct(r.vol_annual_pct)}</div><div class="x">{pct(r.vol_pct)} at {p.horizon}</div></div>
    <div class="kpi"><div class="k">Beta</div><div class="v">{num(r.beta)}</div><div class="x">vs NIFTY</div></div>
    <div class="kpi bad"><div class="k">99% VaR · 1d</div><div class="v">{pct(r.var_99_1d_pct)}</div><div class="x">CVaR {pct(r.cvar_99_pct)}</div></div>
    <div class="kpi"><div class="k">Eff. holdings</div><div class="v">{num(r.effective_holdings)}</div><div class="x">of {p.cards.length}</div></div>
    <div class="kpi warn"><div class="k">Top risk</div><div class="v">{pct(r.top_risk_pct)}</div><div class="x">{(r.top_risk_contributor ?? '—').replace('.NS','')}</div></div>
  </div>

  <div class="grid3">
    <div class="panel">
      <div class="panel-h"><h2>Risk contribution vs weight</h2><span class="hint">%CTR (red) · capital weight (blue)</span></div>
      {#if rk?.positions}<Chart option={riskContribOption(rk.positions)} height="300px" />{/if}
    </div>
    <div class="panel">
      <div class="panel-h"><h2>Sector exposure</h2><span class="hint">{s?.concentration ?? '—'}</span></div>
      {#if s?.sectors?.length}<Chart option={sectorDonutOption(s.sectors)} height="300px" />{/if}
    </div>
  </div>

  {#if rk?.correlation && Object.keys(rk.correlation).length > 1}
    <div class="panel">
      <div class="panel-h"><h2>Correlation matrix</h2><span class="hint">avg pairwise {num(r.avg_pairwise_corr)}</span></div>
      <Chart option={corrHeatmapOption(rk.correlation)} height="320px" />
    </div>
  {/if}

  <div class="panel-h" style="margin-top:0.5rem"><h2>Holdings</h2><span class="hint">ranked by risk contribution · click to drill in</span></div>
  <div class="cards">
    {#each p.cards as c}
      {@const t = c.technical?.dials ?? {}}
      {@const f = c.fundamental?.dials ?? {}}
      {@const a = c.attention ?? {}}
      <a class="card" href={`/stock/${c.symbol}`}>
        <div class="top">
          <div>
            <div class="sym">{c.symbol.replace('.NS', '')}</div>
            <div class="sub">wt {pct(c.risk?.weight_pct)} · risk {pct(c.risk?.risk_contribution_pct)} · β {num(c.risk?.beta)}</div>
          </div>
          <span class="attn {attnClass(a.score)}">{a.score == null ? '—' : Math.round(a.score)}</span>
        </div>
        <div class="dials">
          {#if t.trend}<span class="dial {dialClass(t.trend)}">{t.trend}</span>{/if}
          {#if t.momentum}<span class="dial {dialClass(t.momentum)}">{t.momentum}</span>{/if}
          {#if t.level}<span class="dial {dialClass(t.level)}">{t.level}</span>{/if}
          {#if f.earnings_growth}<span class="dial {dialClass(f.earnings_growth)}">earnings {f.earnings_growth}</span>{/if}
          {#if c.sentiment?.label}<span class="dial {dialClass(c.sentiment.label)}">news {c.sentiment.label}</span>{/if}
        </div>
        {#if a.drivers?.length}<div class="drivers">{a.drivers.slice(0, 2).join(' · ')}</div>{/if}
        {#if c.confidence}<div class="conf">confidence {c.confidence.level} ({num(c.confidence.score)})</div>{/if}
        {#if c.delta?.changes?.length}<div class="delta">Δ {c.delta.changes.join('; ')}</div>{/if}
      </a>
    {/each}
  </div>

  <footer>
    Descriptive only — risk/technicals/fundamentals explained, never a buy/sell. Free public data ·
    point-in-time · sentiment &amp; ownership are current snapshots (not back-tested).
  </footer>
{/if}
