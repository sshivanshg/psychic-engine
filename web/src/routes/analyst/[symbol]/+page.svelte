<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api';
  import { num, pct, signed, dialClass, attnClass } from '$lib/format';
  import { theme } from '$lib/theme';
  import Chart from '$lib/Chart.svelte';
  import NewsFeed from '$lib/NewsFeed.svelte';
  import QuarterTable from '$lib/QuarterTable.svelte';
  import SentimentBar from '$lib/SentimentBar.svelte';
  import ProvenanceBadge from '$lib/ProvenanceBadge.svelte';
  import {
    priceChartOption, rsiGauge, attentionGauge, returnsBarOption,
    quarterlyTrendOption, ownershipDonutOption, polarityTimelineOption
  } from '$lib/charts';

  const symbol = $page.params.symbol ?? '';

  let data = $state<any>(null); // /api/analyst — {symbol, as_of, facts, verdict, usage}
  let series = $state<any>(null);
  let newsData = $state<any>(null);
  let docs = $state<any[]>([]);
  let bookCard = $state<any>(null); // portfolio-context card (weight / risk-contribution / delta) when held
  let loading = $state(true);
  let error = $state<string | null>(null);
  let tab = $state('overview');

  // past briefs (persisted analyst_runs)
  let history = $state<any[]>([]);
  let openRun = $state<number | null>(null);

  // verdict (opt-in LLM call)
  let verdict = $state<any>(null);
  let usage = $state<any>(null);
  let verdictLoading = $state(false);
  let verdictErr = $state<string | null>(null);

  // RAG ask-the-call (filings only — Documents tab)
  let question = $state('');
  let answer = $state<any>(null);
  let asking = $state(false);

  // deep multi-agent analysis (bull · bear · sector → judge) — the Analysis tab
  let deep = $state<any>(null);
  let debate = $state<any>(null);
  let deepUsage = $state<any>(null);
  let deepCost = $state<number | null>(null);
  let deepModel = $state<string | null>(null);
  let deepLoading = $state(false);
  let deepErr = $state<string | null>(null);
  let openAgent = $state<string | null>(null);

  // ask-the-analyst (whole research + live web) — a small chat thread on the Analysis tab
  let aq = $state('');
  let aThread = $state<any[]>([]);
  let aAsking = $state(false);

  const f = $derived(data?.facts ?? null);
  const pricedThrough = $derived(series?.dates?.length ? series.dates[series.dates.length - 1] : null);

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'technical', label: 'Technical' },
    { id: 'fundamental', label: 'Fundamentals' },
    { id: 'news', label: 'News & Catalysts' },
    { id: 'ownership', label: 'Ownership' },
    { id: 'risk', label: 'Risk' },
    { id: 'docs', label: 'Documents' },
    { id: 'reasoning', label: 'Reasoning' },
    { id: 'analysis', label: 'Analysis' },
    { id: 'history', label: 'History' }
  ];

  // Each analyzer dimension as a "read": the factual one-liner the deterministic agent produced.
  const DIMS = ['technical', 'fundamental', 'risk', 'macro', 'sentiment', 'ownership'];

  onMount(async () => {
    try {
      const [d, s, n, dc, bc, h] = await Promise.all([
        api.analyst(symbol),
        api.stockSeries(symbol).catch(() => null),
        api.stockNews(symbol).catch(() => null),
        api.stockDocs(symbol).catch(() => ({ documents: [] })),
        api.stock(symbol, false).catch(() => null), // 404 if not a current holding — that's fine
        api.analystHistory(symbol).catch(() => ({ runs: [] }))
      ]);
      data = d;
      series = s;
      newsData = n;
      docs = dc?.documents ?? [];
      bookCard = bc?.card ?? null;
      history = h?.runs ?? [];
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });

  async function runVerdict() {
    verdictLoading = true;
    verdictErr = null;
    try {
      const v = await api.analystVerdict(symbol);
      verdict = v.verdict;
      usage = v.usage;
      if (f) data = { ...data, facts: { ...f, credibility: v.facts?.credibility ?? f.credibility } };
      if (!verdict) verdictErr = 'No API key on the server — set ANTHROPIC_API_KEY for the AI verdict.';
      else {
        tab = 'analysis';
        // a verdict run was just persisted server-side — refresh the history list
        api.analystHistory(symbol).then((h) => (history = h.runs ?? [])).catch(() => {});
      }
    } catch (e) {
      verdictErr = (e as Error).message;
    } finally {
      verdictLoading = false;
    }
  }

  async function runDeep() {
    deepLoading = true;
    deepErr = null;
    tab = 'analysis';
    try {
      const d = await api.analystDeep(symbol);
      deep = d.deep;
      debate = d.debate;
      deepUsage = d.usage;
      deepCost = d.cost_usd;
      deepModel = d.model;
      if (!deep) deepErr = 'No API key on the server — set ANTHROPIC_API_KEY for the deep read.';
      else api.analystHistory(symbol).then((h) => (history = h.runs ?? [])).catch(() => {});
    } catch (e) {
      deepErr = (e as Error).message;
    } finally {
      deepLoading = false;
    }
  }

  async function askAnalyst() {
    const q = aq.trim();
    if (!q || aAsking) return;
    aAsking = true;
    aq = '';
    aThread = [...aThread, { q, a: null, pending: true }];
    const i = aThread.length - 1;
    try {
      const res = await api.askResearch(symbol, q);
      aThread[i] = { q, a: res, pending: false };
    } catch (e) {
      aThread[i] = { q, a: { answer: null, note: (e as Error).message, hits: [] }, pending: false };
    } finally {
      aThread = [...aThread];
      aAsking = false;
    }
  }

  async function ask() {
    if (!question.trim()) return;
    asking = true;
    answer = null;
    try {
      answer = await api.ask(symbol, question);
    } catch (e) {
      answer = { answer: null, note: (e as Error).message, hits: [] };
    } finally {
      asking = false;
    }
  }

  // a compact factual read per dimension, straight from the deterministic agent output
  function readOf(dim: string): string {
    if (!f) return '';
    if (dim === 'technical') {
      const t = f.technical ?? {}, d = t.dials ?? {};
      return `${d.trend ?? '—'} · ${d.momentum ?? '—'} (RSI ${num(t.rsi_14, 0)}) · ${d.level ?? '—'} · ${pct(t.price_vs_sma200_pct)} vs 200SMA`;
    }
    if (dim === 'fundamental') {
      const fn = f.fundamental;
      if (!fn) return 'no quarterly fundamentals on file';
      const d = fn.dials ?? {};
      return `revenue ${d.revenue_growth ?? '—'} (YoY ${pct(fn.revenue_yoy_pct)}) · earnings ${d.earnings_growth ?? '—'} · margin ${d.margin_trend ?? '—'} (net ${pct(fn.net_margin_pct)})`;
    }
    if (dim === 'risk') {
      const r = f.risk ?? {};
      return `vol ${pct(r.vol_pct)} · β ${num(r.beta)} · max DD ${pct(r.max_drawdown_pct)} · ${num(r.liquidity_days, 1)}d to liquidate`;
    }
    if (dim === 'macro') {
      const m = f.macro ?? {};
      return `sector ${m.sector ?? '—'}`;
    }
    if (dim === 'sentiment') {
      const sn = f.sentiment;
      return sn ? `${sn.label} flow · ${sn.n_articles} headlines · mean ${signed(sn.mean_polarity, 2)}` : 'no news ingested';
    }
    if (dim === 'ownership') {
      const o = f.ownership;
      return o ? `institutional ${pct(o.institutional_pct)} (${o.dials?.institutional ?? '—'}) · insider ${pct(o.insider_pct)}` : 'no ownership snapshot';
    }
    return '';
  }
</script>

<div class="crumb"><a href="/">← Portfolio</a> · <a href="/news">Newsroom</a></div>

{#if loading}
  <div class="loading">Assembling every fetched detail for {symbol.replace('.NS', '')}…</div>
{:else if error}
  <div class="err">{error}
    <div class="note" style="margin-top:0.5rem">Is the API up? <code>uv run tradeos serve</code> — and is {symbol} ingested?</div>
  </div>
{:else if f}
  {@const att = f.attention ?? {}}
  {@const conf = f.confidence ?? {}}
  {@const r = f.risk ?? {}}

  <!-- ───────── sticky header: identity · key stats · verdict ───────── -->
  <div class="wb-head">
    <div class="wb-id">
      <h1>{symbol.replace('.NS', '')}</h1>
      <div class="sub">
        {f.macro?.sector ?? '—'} · ₹{num(f.last_close)}
        {#if pricedThrough}· priced through {pricedThrough}{/if}
      </div>
    </div>
    <div class="wb-stats">
      {#if bookCard}
        <div class="st"><span class="k">weight</span><span class="v">{pct(bookCard.risk?.weight_pct)}</span></div>
        <div class="st"><span class="k">risk %CTR</span><span class="v">{pct(bookCard.risk?.risk_contribution_pct)}</span></div>
      {/if}
      <div class="st"><span class="k">vol</span><span class="v">{pct(r.vol_pct)}</span></div>
      <div class="st"><span class="k">β</span><span class="v">{num(r.beta)}</span></div>
      <div class="st"><span class="k">attention</span><span class="attn {attnClass(att.score)}">{att.score == null ? '—' : Math.round(att.score)}</span></div>
      <div class="st"><span class="k">read-conf</span><span class="v">{conf.level ?? '—'}</span></div>
    </div>
    <div class="wb-verdict">
      {#if deep}
        <div class="vline">▸ {deep.headline}</div>
        <div class="vconf">confidence: {deep.confidence} · <button class="linklike" onclick={() => (tab = 'analysis')}>full analysis ↓</button></div>
      {:else if verdict}
        <div class="vline">▸ {verdict.one_line}</div>
        <div class="vconf">quick read · <button class="linklike" onclick={runDeep} disabled={deepLoading}>{deepLoading ? 'deep read…' : 'go deeper ↓'}</button></div>
      {:else}
        <button onclick={runDeep} disabled={deepLoading}>
          {deepLoading ? 'Running…' : 'Run deep analysis'}
        </button>
        <div class="note">bull · bear · sector → judge · ~$0.05–0.10</div>
        {#if deepErr}<div class="note bad">{deepErr}</div>{/if}
      {/if}
    </div>
  </div>

  {#if bookCard?.delta?.changes?.length}
    <div class="panel delta-panel"><strong>Δ since last run</strong> · <span class="delta">{bookCard.delta.changes.join('; ')}</span></div>
  {/if}

  <!-- ───────── evidence tabs ───────── -->
  <div class="tabs">
    {#each TABS as t}
      <button class="tab" class:active={tab === t.id} onclick={() => (tab = t.id)}>{t.label}</button>
    {/each}
  </div>

  <!-- ===== OVERVIEW ===== -->
  {#if tab === 'overview'}
    <div class="grid2">
      <div class="panel">
        <div class="panel-h"><h2>Every dimension at a glance</h2><ProvenanceBadge asOf={data.as_of ?? pricedThrough} pit={true} /></div>
        {#each DIMS as dim}
          <div class="read-row">
            <span class="read-dim">{dim}</span>
            <span class="read-txt">{readOf(dim)}</span>
          </div>
        {/each}
      </div>
      <div class="panel">
        <div class="panel-h"><h2>Why it draws attention</h2><span class="hint">decomposed · 0–100</span></div>
        {#each DIMS as dim}
          {#if att.components?.[dim] != null}
            <div class="comp-row">
              <span class="cl">{dim}</span>
              <span class="comp-bar"><span style="width:{att.components[dim]}%; background:{att.components[dim] >= 66 ? 'var(--bad)' : att.components[dim] >= 40 ? 'var(--warn)' : 'var(--good)'}"></span></span>
              <span class="comp-v">{Math.round(att.components[dim])}</span>
            </div>
          {/if}
        {/each}
        {#if att.drivers?.length}<div class="drivers">{att.drivers.join(' · ')}</div>{/if}
        {#if conf.reasons?.length}<div class="conf">read-confidence {conf.level} — {conf.reasons.join('; ')}</div>{/if}
      </div>
    </div>
  {/if}

  <!-- ===== TECHNICAL ===== -->
  {#if tab === 'technical'}
    {@const t = f.technical ?? {}}
    {@const td = t.dials ?? {}}
    <div class="grid3">
      <div class="panel">
        <div class="panel-h"><h2>Price &amp; moving averages</h2><span class="hint">{td.trend ?? ''} · {pct(t.pct_from_52w_high)} from 52w high</span></div>
        {#if series}{#key $theme}<Chart option={priceChartOption(series)} height="340px" />{/key}{:else}<div class="note">no price series</div>{/if}
      </div>
      <div>
        <div class="panel" style="margin-bottom:1rem">{#key $theme}<Chart option={rsiGauge(t.rsi_14 ?? null)} height="150px" />{/key}</div>
        <div class="panel">{#key $theme}<Chart option={returnsBarOption({ technical: t })} height="150px" />{/key}</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-h"><h2>Indicators</h2><div class="dials">
        <span class="dial {dialClass(td.trend)}">{td.trend ?? '—'}</span>
        <span class="dial {dialClass(td.momentum)}">{td.momentum ?? '—'}</span>
        <span class="dial {dialClass(td.level)}">{td.level ?? '—'}</span>
      </div></div>
      <div class="kv">
        <div class="spread"><span class="k">Last close</span><span class="v">₹{num(t.last_close)}</span></div>
        <div class="spread"><span class="k">SMA 20 / 50 / 200</span><span class="v">{num(t.sma20)} / {num(t.sma50)} / {num(t.sma200)}</span></div>
        <div class="spread"><span class="k">Price vs 200-day SMA</span><span class="v">{pct(t.price_vs_sma200_pct)}</span></div>
        <div class="spread"><span class="k">RSI(14)</span><span class="v">{num(t.rsi_14, 1)}</span></div>
        <div class="spread"><span class="k">MACD histogram</span><span class="v">{num(t.macd_hist)}</span></div>
        <div class="spread"><span class="k">Return 1m / 3m</span><span class="v">{pct(t.ret_1m_pct)} / {pct(t.ret_3m_pct)}</span></div>
        <div class="spread"><span class="k">From 52-week high</span><span class="v">{pct(t.pct_from_52w_high)}</span></div>
        <div class="spread"><span class="k">Volume trend</span><span class="v">{t.volume_trend ?? '—'}</span></div>
      </div>
    </div>
  {/if}

  <!-- ===== FUNDAMENTAL ===== -->
  {#if tab === 'fundamental'}
    {@const fn = f.fundamental}
    {@const qt = f.quarterly_trend ?? []}
    {#if qt.length}
      <div class="panel">
        <div class="panel-h"><h2>Quarterly results trend</h2><span class="hint">last {qt.length} quarters · free, deterministic</span></div>
        {#key $theme}<Chart option={quarterlyTrendOption(qt)} height="260px" />{/key}
      </div>
    {/if}
    <div class="grid2">
      <div class="panel">
        <div class="panel-h"><h2>Reported quarters</h2></div>
        <QuarterTable rows={qt} />
      </div>
      <div class="panel">
        <div class="panel-h"><h2>Latest quarter &amp; growth</h2></div>
        {#if fn}
          <div class="dials" style="margin-bottom:0.4rem">
            <span class="dial {dialClass(fn.dials?.revenue_growth)}">revenue {fn.dials?.revenue_growth ?? '—'}</span>
            <span class="dial {dialClass(fn.dials?.earnings_growth)}">earnings {fn.dials?.earnings_growth ?? '—'}</span>
            <span class="dial {dialClass(fn.dials?.margin_trend)}">margin {fn.dials?.margin_trend ?? '—'}</span>
          </div>
          <div class="spread"><span class="k">Latest quarter</span><span class="v">{fn.latest_quarter ?? '—'}</span></div>
          <div class="spread"><span class="k">Revenue YoY / QoQ</span><span class="v">{pct(fn.revenue_yoy_pct)} / {pct(fn.revenue_qoq_pct)}</span></div>
          <div class="spread"><span class="k">Net income YoY</span><span class="v">{pct(fn.net_income_yoy_pct)}</span></div>
          <div class="spread"><span class="k">Net / op margin</span><span class="v">{pct(fn.net_margin_pct)} / {pct(fn.op_margin_pct)}</span></div>
          <div class="spread"><span class="k">Net-margin change</span><span class="v">{num(fn.net_margin_change_pp)} pp</span></div>
          {#if fn.guidance}
            <div class="guidance">
              <div class="lbl">Guidance <span class="tag">{fn.guidance.source}</span></div>
              {#if fn.guidance.revenue_outlook}<div>· revenue: {fn.guidance.revenue_outlook}</div>{/if}
              {#if fn.guidance.margin_outlook}<div>· margin: {fn.guidance.margin_outlook}</div>{/if}
              {#if fn.guidance.demand_commentary}<div>· demand: {fn.guidance.demand_commentary}</div>{/if}
              {#each fn.guidance.quotes ?? [] as q}<div class="quote">“{q}”</div>{/each}
            </div>
          {/if}
        {:else}<span class="muted">no quarterly fundamentals on file (ingest concall PDFs for depth)</span>{/if}
      </div>
    </div>
  {/if}

  <!-- ===== NEWS & CATALYSTS ===== -->
  {#if tab === 'news'}
    {@const cats = f.catalysts ?? []}
    <div class="panel">
      <div class="panel-h"><h2>News flow</h2><ProvenanceBadge asOf={newsData?.as_of} snapshot={true} /></div>
      <SentimentBar summary={newsData?.summary ?? f.sentiment} />
      {#if (newsData?.headlines ?? []).some((h: any) => h.polarity != null)}
        <div style="margin-top:0.6rem">{#key $theme}<Chart option={polarityTimelineOption(newsData.headlines)} height="150px" />{/key}</div>
      {/if}
    </div>
    {#if cats.length}
      <div class="panel">
        <div class="panel-h"><h2>Tagged catalysts</h2><span class="hint">{cats.length} · keyword-classified, free</span></div>
        <div class="cat-list">
          {#each cats as c}
            <div class="cat-row">
              <span class="evt evt-{(c.event ?? '').split('/')[0]}">{c.event}</span>
              <span class="cat-date">{c.date ?? '—'}</span>
              <span class="cat-title">{c.title}</span>
              <span class="pol {c.polarity > 0 ? 'pol-pos' : c.polarity < 0 ? 'pol-neg' : 'pol-zero'}">{c.polarity == null ? '' : signed(c.polarity, 2)}</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}
    <div class="panel">
      <div class="panel-h"><h2>All fetched headlines</h2><span class="hint">{(newsData?.headlines ?? []).length}</span></div>
      <NewsFeed headlines={newsData?.headlines ?? []} />
    </div>
  {/if}

  <!-- ===== OWNERSHIP ===== -->
  {#if tab === 'ownership'}
    {@const o = f.ownership}
    <div class="grid2">
      <div class="panel">
        <div class="panel-h"><h2>Holding structure</h2><ProvenanceBadge snapshot={true} /></div>
        {#if o}
          {#key $theme}<Chart option={ownershipDonutOption(o)} height="240px" />{/key}
        {:else}<span class="muted">no ownership snapshot ingested</span>{/if}
      </div>
      <div class="panel">
        <div class="panel-h"><h2>Detail</h2></div>
        {#if o}
          <div class="dials" style="margin-bottom:0.4rem"><span class="dial {dialClass(o.dials?.institutional)}">institutional {o.dials?.institutional ?? '—'}</span></div>
          <div class="spread"><span class="k">Institutional</span><span class="v">{pct(o.institutional_pct)}</span></div>
          <div class="spread"><span class="k">Insider</span><span class="v">{pct(o.insider_pct)}</span></div>
          <div class="spread"><span class="k"># institutions</span><span class="v">{num(o.n_institutions, 0)}</span></div>
          {#if o.note}<div class="note" style="margin-top:0.5rem">{o.note}</div>{/if}
        {:else}<span class="muted">no data</span>{/if}
      </div>
    </div>
  {/if}

  <!-- ===== RISK ===== -->
  {#if tab === 'risk'}
    <div class="panel">
      <div class="panel-h"><h2>This name's risk</h2><span class="hint">{bookCard ? 'in-book contribution + standalone' : 'standalone (not a current holding)'}</span></div>
      <div class="kv">
        {#if bookCard}
          <div class="spread"><span class="k">Portfolio weight</span><span class="v">{pct(bookCard.risk?.weight_pct)}</span></div>
          <div class="spread"><span class="k">Risk contribution (%CTR)</span><span class="v">{pct(bookCard.risk?.risk_contribution_pct)}</span></div>
        {/if}
        <div class="spread"><span class="k">Volatility (annual)</span><span class="v">{pct(r.vol_pct)}</span></div>
        <div class="spread"><span class="k">Beta vs NIFTY</span><span class="v">{num(r.beta)}</span></div>
        <div class="spread"><span class="k">Max drawdown</span><span class="v">{pct(r.max_drawdown_pct)}</span></div>
        <div class="spread"><span class="k">Days to liquidate</span><span class="v">{num(r.liquidity_days, 1)}</span></div>
      </div>
      <div class="note" style="margin-top:0.6rem">Standalone vol/β are point-in-time from price history. Portfolio-relative fields show only when {symbol.replace('.NS', '')} is a current holding.</div>
    </div>
  {/if}

  <!-- ===== DOCUMENTS (RAG) ===== -->
  {#if tab === 'docs'}
    <div class="panel">
      <div class="panel-h"><h2>Ingested documents</h2><span class="hint">{docs.length} source(s) · the RAG corpus</span></div>
      {#if docs.length}
        <table>
          <thead><tr><th>Source</th><th>Period</th><th>Filing date</th><th>Chunks</th><th>Ingested</th></tr></thead>
          <tbody>
            {#each docs as d}
              <tr>
                <td>{d.source_url ? `${d.source}` : d.source}</td>
                <td>{d.period ?? '—'}</td>
                <td>{d.filing_date ?? '—'}</td>
                <td>{d.chunks}</td>
                <td>{d.ingested_at ?? '—'}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <span class="muted">no documents ingested — add a concall/results PDF on the Manage page or via <code>tradeos docs add {symbol} file.pdf</code></span>
      {/if}
    </div>
    <div class="panel">
      <div class="panel-h"><h2>Ask the filings</h2><span class="tag">RAG over filings/concalls only · cited</span></div>
      <div class="note" style="margin-bottom:0.5rem">Grounded strictly in ingested documents. For a question over the <em>whole</em> research (facts + deep read + live web), use <button class="linklike" onclick={() => (tab = 'analysis')}>Ask the analyst</button> on the Analysis tab.</div>
      <div class="row">
        <input style="flex:1; min-width:240px" placeholder="e.g. what did management say about margins?"
               bind:value={question} onkeydown={(e) => e.key === 'Enter' && ask()} />
        <button onclick={ask} disabled={asking}>{asking ? '…' : 'Ask'}</button>
      </div>
      {#if answer}
        {#if answer.note}<div class="note" style="margin-top:0.6rem">⚠ {answer.note}</div>{/if}
        {#if answer.answer}
          <p style="margin-top:0.6rem">{answer.answer}</p>
          {#if answer.citations?.length}<div class="muted mono">cited: {answer.citations.map((c: number) => `[${c}]`).join(' ')}</div>{/if}
        {/if}
        {#if answer.hits?.length}
          <div class="muted" style="margin-top:0.6rem">Retrieved excerpts:</div>
          {#each answer.hits as h}<div class="note">[{h.chunk}] {h.source} (d={h.distance}): {h.content?.slice(0, 200)}…</div>{/each}
        {/if}
      {/if}
    </div>
  {/if}

  <!-- ===== REASONING ===== -->
  {#if tab === 'reasoning'}
    <div class="panel">
      <div class="panel-h"><h2>Agent reads</h2><span class="hint">6 deterministic analyzers — the factual read each produced</span></div>
      <div class="reads">
        {#each DIMS as dim}
          <div class="read-card">
            <div class="rc-h"><span class="rc-name">{dim}</span>{#if att.components?.[dim] != null}<span class="rc-score">{Math.round(att.components[dim])}</span>{/if}</div>
            <div class="rc-txt">{readOf(dim)}</div>
          </div>
        {/each}
      </div>
      <div class="note" style="margin-top:0.7rem">These are pure compute (no LLM) over one point-in-time data load. Only the optional verdict synthesises. Watch a full book run live on the <a href="/live">Reasoning Monitor</a>.</div>
    </div>
    {#if f.credibility}
      <div class="panel">
        <div class="panel-h"><h2>Management credibility</h2><span class="hint">guidance → delivered</span></div>
        <div class="spread"><span class="k">Track record</span><span class="v">{f.credibility.track_record ?? '—'}</span></div>
        {#each f.credibility.checks ?? [] as ch}
          <div class="cred-row"><span class="evt">{ch.verdict}</span> {ch.period}: promised {ch.promised} → actual {ch.actual}</div>
        {/each}
        {#if f.credibility.caveat}<div class="note">{f.credibility.caveat}</div>{/if}
      </div>
    {/if}
  {/if}

  <!-- ===== ANALYSIS (deep multi-agent read + ask the analyst) ===== -->
  {#if tab === 'analysis'}
    {#if !deep}
      <div class="panel">
        <div class="panel-h"><h2>Deep analysis</h2><span class="tag">bull · bear · sector → judge · LLM</span></div>
        <p>The deterministic evidence in the other tabs is complete and free. The deep read runs four
          agents — a bull, a bear and a sector analyst reason over the same facts, then a judge
          reconciles them into what's genuinely right, what's wrong, how the sector bears on the name,
          and descriptive scenarios. Descriptive only — you make the call.</p>
        <div class="row">
          <button onclick={runDeep} disabled={deepLoading}>{deepLoading ? 'Running the desk…' : 'Run deep analysis'}</button>
          <span class="note">4 {deepModel ?? 'Sonnet'} calls · ~$0.05–0.10</span>
        </div>
        {#if deepErr}<div class="note bad" style="margin-top:0.5rem">{deepErr}</div>{/if}
        {#if verdict}
          <div class="quick-read">
            <div class="qr-h">Quick read <span class="tag">1 Haiku call</span></div>
            <div class="vbig">▸ {verdict.one_line}</div>
            <div class="quarter">quarter: {verdict.quarter_read}</div>
            <div class="bb">
              <div class="bb-col bull"><div class="bb-h">Bull</div>{#each verdict.bull as b}<div class="bb-item">{b}</div>{/each}</div>
              <div class="bb-col bear"><div class="bb-h">Bear</div>{#each verdict.bear as b}<div class="bb-item">{b}</div>{/each}</div>
              <div class="bb-col watch"><div class="bb-h">Watch</div>{#each verdict.watch as w}<div class="bb-item">{w}</div>{/each}</div>
            </div>
            <div class="vconf">confidence: {verdict.confidence}</div>
          </div>
        {:else}
          <button class="btn-ghost" style="margin-top:0.6rem" onclick={runVerdict} disabled={verdictLoading}>
            {verdictLoading ? 'Quick read…' : 'or just a quick one-line read · ~$0.003'}</button>
          {#if verdictErr}<div class="note bad" style="margin-top:0.5rem">{verdictErr}</div>{/if}
        {/if}
      </div>
    {:else}
      <div class="panel deep-head">
        <div class="panel-h"><h2>Deep analysis</h2><ProvenanceBadge asOf={data.as_of ?? pricedThrough} pit={true} /></div>
        <div class="vbig">▸ {deep.headline}</div>
        <p class="thesis">{deep.thesis}</p>
        <div class="vconf">confidence: {deep.confidence}</div>
      </div>

      <div class="grid2">
        <div class="panel">
          <div class="panel-h"><h2 style="color:var(--good)">What's right</h2></div>
          {#if deep.whats_right?.length}<ul class="rw rw-good">{#each deep.whats_right as x}<li>{x}</li>{/each}</ul>{:else}<span class="muted">—</span>{/if}
        </div>
        <div class="panel">
          <div class="panel-h"><h2 style="color:var(--bad)">What's wrong</h2></div>
          {#if deep.whats_wrong?.length}<ul class="rw rw-bad">{#each deep.whats_wrong as x}<li>{x}</li>{/each}</ul>{:else}<span class="muted">—</span>{/if}
        </div>
      </div>

      <div class="panel">
        <div class="panel-h"><h2>Sector &amp; how it should perform</h2><span class="hint">descriptive scenarios · no live sector feed</span></div>
        <p>{deep.sector_context}</p>
        {#if deep.scenarios?.length}
          <div class="scen-grid">
            {#each deep.scenarios as s}
              <div class="scen">
                <div class="scen-h">{s.label}</div>
                <ul>{#each s.drivers as d}<li>{d}</li>{/each}</ul>
                <div class="scen-imp">⇒ {s.implication}</div>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <div class="grid2">
        <div class="panel">
          <div class="panel-h"><h2>Latest quarter</h2></div>
          <p>{deep.quarter_read}</p>
        </div>
        {#if deep.what_to_watch?.length}
          <div class="panel">
            <div class="panel-h"><h2>What to watch</h2></div>
            <ul class="watch-list">{#each deep.what_to_watch as w}<li>{w}</li>{/each}</ul>
          </div>
        {/if}
      </div>

      <div class="panel bottom-panel">
        <div class="panel-h"><h2>Bottom line</h2></div>
        <p class="bottom">{deep.bottom_line}</p>
        <div class="row" style="justify-content:space-between; margin-top:0.4rem">
          {#if deepCost != null}<span class="note">4 {deepModel} calls · {deepUsage?.input_tokens ?? '—'} in / {deepUsage?.output_tokens ?? '—'} out tok · ~${deepCost}</span>{/if}
          <button class="btn-ghost" onclick={runDeep} disabled={deepLoading}>{deepLoading ? 'Re-running…' : 'Re-run'}</button>
        </div>
      </div>

      {#if debate}
        <div class="panel">
          <div class="panel-h"><h2>The desk's debate</h2><span class="hint">each agent's raw read — click to expand</span></div>
          {#each ['bull', 'bear', 'sector'] as name}
            {#if debate[name]}
              <div class="agent-row">
                <button class="agent-head" onclick={() => (openAgent = openAgent === name ? null : name)}>
                  <span class="agent-name {name}">{name} agent</span>
                  <span class="agent-sum">{debate[name].summary ?? debate[name].backdrop ?? ''}</span>
                  <span class="hist-caret">{openAgent === name ? '▾' : '▸'}</span>
                </button>
                {#if openAgent === name}
                  <div class="agent-body">
                    {#if debate[name].points}
                      {#each debate[name].points as p}<div class="cp"><span class="cp-pt">{p.point}</span><span class="cp-ev">{p.evidence}</span></div>{/each}
                    {:else}
                      <div class="spread"><span class="k">backdrop</span><span class="v">{debate[name].backdrop}</span></div>
                      <div class="spread"><span class="k">company fit</span><span class="v">{debate[name].company_fit}</span></div>
                      <div class="spread"><span class="k">sensitivity</span><span class="v">{debate[name].sensitivity}</span></div>
                    {/if}
                  </div>
                {/if}
              </div>
            {/if}
          {/each}
        </div>
      {/if}
    {/if}

    <!-- ask the analyst: a follow-up over the WHOLE research (facts + deep read + filings + live web) -->
    <div class="panel">
      <div class="panel-h"><h2>Ask the analyst</h2><span class="tag">whole research · filings + live web · cited</span></div>
      <div class="row">
        <input style="flex:1; min-width:240px" placeholder="e.g. how would a demand slowdown hit margins?"
               bind:value={aq} onkeydown={(e) => e.key === 'Enter' && askAnalyst()} />
        <button onclick={askAnalyst} disabled={aAsking}>{aAsking ? '…' : 'Ask'}</button>
      </div>
      {#each aThread as turn}
        <div class="qa">
          <div class="qa-q">{turn.q}</div>
          {#if turn.pending}
            <div class="note">thinking… (the analyst may web-search — a few seconds)</div>
          {:else if turn.a}
            {#if turn.a.answer}<p class="qa-a">{turn.a.answer}</p>{/if}
            {#if turn.a.note}<div class="note">⚠ {turn.a.note}</div>{/if}
            <div class="qa-meta">
              {#if turn.a.web_used}<span class="tag">used live web</span>{/if}
              {#if turn.a.citations?.length}<span class="muted mono">cited: {turn.a.citations.map((c: number) => `[${c}]`).join(' ')}</span>{/if}
            </div>
            {#if turn.a.web_sources?.length}
              <div class="srcs">{#each turn.a.web_sources as s}<a class="src" href={s.url} target="_blank" rel="noreferrer">↗ {s.title}</a>{/each}</div>
            {/if}
            {#if turn.a.hits?.length}
              <details class="excerpts"><summary>retrieved filing excerpts ({turn.a.hits.length})</summary>
                {#each turn.a.hits as h, i}<div class="note">[{i + 1}] {h.source}: {h.content?.slice(0, 200)}…</div>{/each}
              </details>
            {/if}
          {/if}
        </div>
      {/each}
      {#if !aThread.length}<div class="note" style="margin-top:0.5rem">Asks one agent about the entire research — the facts, the deep read, the filings, and the live web. Descriptive only.</div>{/if}
    </div>
  {/if}

  <!-- ===== HISTORY (past briefs) ===== -->
  {#if tab === 'history'}
    <div class="panel">
      <div class="panel-h"><h2>Past briefs</h2><span class="hint">{history.length} saved · newest first · click a row to expand</span></div>
      {#if history.length}
        {#each history as run}
          {@const snap = run.snapshot ?? {}}
          {@const vd = run.verdict ?? {}}
          {@const dp = run.deep ?? null}
          <div class="hist-row">
            <button class="hist-head" onclick={() => (openRun = openRun === run.id ? null : run.id)}>
              <span class="hist-when">{(run.run_at ?? '').slice(0, 16).replace('T', ' ')}</span>
              <span class="hist-line">{run.one_line}</span>
              {#if snap.attention != null}<span class="attn {attnClass(snap.attention)}">{Math.round(snap.attention)}</span>{/if}
              <span class="hist-cost">${run.cost_usd ?? '—'}</span>
              <span class="hist-caret">{openRun === run.id ? '▾' : '▸'}</span>
            </button>
            {#if openRun === run.id}
              <div class="hist-body">
                <div class="dials" style="margin-bottom:0.5rem">
                  {#if snap.trend}<span class="dial {dialClass(snap.trend)}">{snap.trend}</span>{/if}
                  {#if snap.momentum}<span class="dial {dialClass(snap.momentum)}">{snap.momentum}</span>{/if}
                  {#if snap.margin_trend}<span class="dial {dialClass(snap.margin_trend)}">margin {snap.margin_trend}</span>{/if}
                  {#if snap.news_label}<span class="dial">news {snap.news_label}</span>{/if}
                  <span class="muted">read-conf {snap.confidence ?? '—'} · {run.model}</span>
                </div>
                {#if dp}
                  {#if dp.thesis}<p class="thesis" style="margin:0 0 0.5rem">{dp.thesis}</p>{/if}
                  <div class="grid2">
                    <div><div class="bb-h" style="color:var(--good)">What's right</div><ul class="rw rw-good">{#each dp.whats_right ?? [] as x}<li>{x}</li>{/each}</ul></div>
                    <div><div class="bb-h" style="color:var(--bad)">What's wrong</div><ul class="rw rw-bad">{#each dp.whats_wrong ?? [] as x}<li>{x}</li>{/each}</ul></div>
                  </div>
                  {#if dp.bottom_line}<div class="note" style="margin-top:0.5rem">bottom line: {dp.bottom_line}</div>{/if}
                  {#if dp.confidence}<div class="vconf">confidence: {dp.confidence}</div>{/if}
                {:else}
                  {#if vd.quarter_read}<div class="quarter">quarter: {vd.quarter_read}</div>{/if}
                  <div class="bb">
                    <div class="bb-col bull"><div class="bb-h">Bull</div>{#each vd.bull ?? [] as b}<div class="bb-item">{b}</div>{/each}</div>
                    <div class="bb-col bear"><div class="bb-h">Bear</div>{#each vd.bear ?? [] as b}<div class="bb-item">{b}</div>{/each}</div>
                    <div class="bb-col watch"><div class="bb-h">Watch</div>{#each vd.watch ?? [] as w}<div class="bb-item">{w}</div>{/each}</div>
                  </div>
                  {#if vd.confidence}<div class="vconf">confidence: {vd.confidence}</div>{/if}
                {/if}
                {#if run.credibility?.track_record}<div class="note" style="margin-top:0.5rem">credibility: {run.credibility.track_record}</div>{/if}
              </div>
            {/if}
          </div>
        {/each}
      {:else}
        <span class="muted">No saved briefs yet. Run the AI verdict and each one is saved here — so you can watch how the read, credibility and news change over time.</span>
      {/if}
    </div>
  {/if}

  <footer>
    Descriptive only — every number is point-in-time and sourced; news &amp; ownership are current
    snapshots (eval-barred). You make the call.
  </footer>
{/if}

<style>
  .hist-row { border-top: 1px solid var(--border, rgba(255, 255, 255, 0.08)); }
  .hist-head {
    display: flex; align-items: center; gap: 0.75rem; width: 100%;
    background: none; border: none; color: inherit; text-align: left;
    padding: 0.6rem 0.2rem; cursor: pointer; font: inherit;
  }
  .hist-head:hover { background: var(--hover, rgba(255, 255, 255, 0.04)); }
  .hist-when { font-variant-numeric: tabular-nums; color: var(--muted, #888); font-size: 0.82em; white-space: nowrap; }
  .hist-line { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hist-cost { color: var(--muted, #888); font-size: 0.82em; font-variant-numeric: tabular-nums; }
  .hist-caret { color: var(--muted, #888); }
  .hist-body { padding: 0.2rem 0.2rem 0.9rem; }

  /* ---- deep analysis ---- */
  .linklike { background: none; border: 0; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
  .linklike:hover { text-decoration: underline; }
  .deep-head .thesis { font-size: 0.92rem; line-height: 1.6; margin: 0.4rem 0 0.6rem; }
  .quick-read { margin-top: 1rem; padding-top: 0.8rem; border-top: 1px dashed var(--border); }
  .quick-read .qr-h { font-size: 0.78rem; font-weight: 600; color: var(--muted); margin-bottom: 0.4rem; }
  .rw { margin: 0; padding-left: 1.1rem; }
  .rw li { font-size: 0.86rem; line-height: 1.55; padding: 0.12rem 0; }
  .rw-good li::marker { color: var(--good); }
  .rw-bad li::marker { color: var(--bad); }
  .scen-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.8rem; margin-top: 0.8rem; }
  @media (max-width: 820px) { .scen-grid { grid-template-columns: 1fr; } }
  .scen { border: 1px solid var(--border); border-radius: 10px; padding: 0.7rem 0.8rem; background: var(--panel-2); }
  .scen-h { font-weight: 600; font-size: 0.84rem; margin-bottom: 0.35rem; }
  .scen ul { margin: 0 0 0.4rem; padding-left: 1.1rem; }
  .scen li { font-size: 0.8rem; line-height: 1.5; color: var(--muted); }
  .scen-imp { font-size: 0.84rem; line-height: 1.5; padding-top: 0.35rem; border-top: 1px solid var(--border-soft); }
  .watch-list { margin: 0; padding-left: 1.1rem; }
  .watch-list li { font-size: 0.86rem; line-height: 1.55; padding: 0.12rem 0; }
  .bottom-panel .bottom { font-size: 0.95rem; line-height: 1.6; margin: 0; }

  /* ---- agent debate ---- */
  .agent-row { border-top: 1px solid var(--border-soft); }
  .agent-head {
    display: flex; align-items: center; gap: 0.7rem; width: 100%;
    background: none; border: none; color: inherit; text-align: left;
    padding: 0.55rem 0.2rem; cursor: pointer; font: inherit;
  }
  .agent-head:hover { background: var(--hover, rgba(255, 255, 255, 0.04)); }
  .agent-name { font-weight: 700; text-transform: capitalize; font-size: 0.82rem; white-space: nowrap; }
  .agent-name.bull { color: var(--good); }
  .agent-name.bear { color: var(--bad); }
  .agent-name.sector { color: var(--accent); }
  .agent-sum { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 0.82rem; }
  .agent-body { padding: 0.1rem 0.2rem 0.7rem; }
  .cp { padding: 0.3rem 0; border-bottom: 1px solid var(--border-soft); }
  .cp:last-child { border-bottom: 0; }
  .cp-pt { display: block; font-size: 0.85rem; line-height: 1.5; }
  .cp-ev { display: block; font-size: 0.76rem; color: var(--muted); font-family: var(--mono); margin-top: 0.1rem; }

  /* ---- ask the analyst ---- */
  .qa { border-top: 1px solid var(--border-soft); margin-top: 0.7rem; padding-top: 0.7rem; }
  .qa-q { font-weight: 600; font-size: 0.88rem; margin-bottom: 0.35rem; }
  .qa-q::before { content: '? '; color: var(--accent); }
  .qa-a { font-size: 0.9rem; line-height: 1.6; margin: 0.2rem 0; white-space: pre-wrap; }
  .qa-meta { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.3rem; }
  .srcs { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.4rem; }
  .src { font-size: 0.78rem; padding: 0.16rem 0.5rem; border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); }
  .excerpts { margin-top: 0.5rem; }
  .excerpts summary { font-size: 0.76rem; color: var(--accent); cursor: pointer; }
</style>
