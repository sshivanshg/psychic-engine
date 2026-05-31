<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { api } from '$lib/api';
  import { num, pct, signed, dialClass } from '$lib/format';
  import Chart from '$lib/Chart.svelte';
  import { priceChartOption, attentionGauge, rsiGauge, returnsBarOption } from '$lib/charts';

  const symbol = $page.params.symbol ?? '';

  let card = $state<any>(null);
  let narrative = $state<any>(null);
  let series = $state<any>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);

  let question = $state('');
  let answer = $state<any>(null);
  let asking = $state(false);

  onMount(async () => {
    try {
      const [res, s] = await Promise.all([api.stock(symbol, true), api.stockSeries(symbol).catch(() => null)]);
      card = res.card;
      narrative = res.narrative;
      series = s;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });

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
</script>

<div class="crumb"><a href="/">← Portfolio</a></div>

{#if loading}
  <div class="loading">Analysing {symbol}…</div>
{:else if error}
  <div class="err">{error}</div>
{:else if card}
  {@const t = card.technical ?? {}}
  {@const td = t.dials ?? {}}
  {@const f = card.fundamental ?? {}}
  {@const fd = f.dials ?? {}}

  <div class="topbar">
    <div>
      <h1>{symbol.replace('.NS', '')}</h1>
      <div class="sub">wt {pct(card.risk?.weight_pct)} · risk contrib {pct(card.risk?.risk_contribution_pct)} · β {num(card.risk?.beta)} · vol {pct(card.risk?.vol_pct)}</div>
    </div>
  </div>

  {#if card.delta?.changes?.length}
    <div class="panel" style="border-color:rgba(240,160,32,0.35)"><strong>Δ since last run</strong> · <span class="delta">{card.delta.changes.join('; ')}</span></div>
  {/if}

  <div class="grid3">
    <div class="panel">
      <div class="panel-h"><h2>Price &amp; moving averages</h2><span class="hint">{td.trend ?? ''} · {pct(t.pct_from_52w_high)} from 52w high</span></div>
      {#if series}<Chart option={priceChartOption(series)} height="340px" />
      {:else}<div class="note">no price series</div>{/if}
    </div>
    <div>
      <div class="panel" style="margin-bottom:1rem"><Chart option={attentionGauge(card.attention?.score ?? null)} height="150px" /></div>
      <div class="panel"><Chart option={rsiGauge(t.rsi_14 ?? null)} height="150px" /></div>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="panel-h"><h2>Technical</h2></div>
      <div class="dials">
        <span class="dial {dialClass(td.trend)}">{td.trend ?? '—'}</span>
        <span class="dial {dialClass(td.momentum)}">{td.momentum ?? '—'}</span>
        <span class="dial {dialClass(td.level)}">{td.level ?? '—'}</span>
      </div>
      <div class="spread"><span class="k">vs 200-day SMA</span><span class="v">{pct(t.price_vs_sma200_pct)}</span></div>
      <div class="spread"><span class="k">MACD histogram</span><span class="v">{num(t.macd_hist)}</span></div>
      <div class="spread"><span class="k">From 52-week high</span><span class="v">{pct(t.pct_from_52w_high)}</span></div>
      <div style="margin-top:0.6rem"><Chart option={returnsBarOption(card)} height="150px" /></div>
    </div>

    <div class="panel">
      <div class="panel-h"><h2>Fundamental</h2></div>
      {#if f.latest_quarter || f.guidance}
        <div class="dials">
          <span class="dial {dialClass(fd.revenue_growth)}">revenue {fd.revenue_growth ?? '—'}</span>
          <span class="dial {dialClass(fd.earnings_growth)}">earnings {fd.earnings_growth ?? '—'}</span>
          <span class="dial {dialClass(fd.margin_trend)}">margin {fd.margin_trend ?? '—'}</span>
        </div>
        <div class="spread"><span class="k">Revenue YoY {f.latest_quarter ? `(${f.latest_quarter})` : ''}</span><span class="v">{pct(f.revenue_yoy_pct)}</span></div>
        <div class="spread"><span class="k">Earnings YoY</span><span class="v">{pct(f.net_income_yoy_pct)}</span></div>
        <div class="spread"><span class="k">Net margin</span><span class="v">{pct(f.net_margin_pct)}</span></div>
        {#if f.guidance}
          <div class="note" style="margin-top:0.6rem">guidance [{f.guidance.source}]: {f.guidance.revenue_outlook ?? ''} {f.guidance.margin_outlook ?? ''}</div>
        {/if}
      {:else}<span class="muted">no quarterly fundamentals on file</span>{/if}
    </div>
  </div>

  <div class="panel">
    <div class="panel-h"><h2>Macro · Sentiment · Ownership</h2></div>
    {#if card.macro}<div class="spread"><span class="k">Sector</span><span class="v">{card.macro.sector ?? '—'} · {pct(card.macro.sector_weight_pct)} of book</span></div>{/if}
    {#if card.sentiment}
      <div class="spread"><span class="k">News flow <span class="tag">snapshot · eval-barred</span></span>
        <span class="v"><span class="dial {dialClass(card.sentiment.label)}">{card.sentiment.label}</span> {card.sentiment.n_articles} hdl, mean {signed(card.sentiment.mean_polarity, 2)}</span></div>
    {/if}
    {#if card.ownership}
      <div class="spread"><span class="k">Ownership <span class="tag">snapshot · eval-barred</span></span>
        <span class="v">institutional {pct(card.ownership.institutional_pct)} ({card.ownership.dials?.institutional ?? '—'}) · insider {pct(card.ownership.insider_pct)}</span></div>
    {/if}
    {#if !card.sentiment && !card.ownership}<span class="muted">no sentiment/ownership ingested — run <code>tradeos ingest</code></span>{/if}
  </div>

  {#if narrative}
    <div class="panel">
      <div class="panel-h"><h2>Reasoning trace</h2><span class="tag">LLM synthesis</span></div>
      <p style="margin:0.3rem 0">{narrative.synthesis}</p>
      {#if narrative.watch_items?.length}<ul>{#each narrative.watch_items as w}<li>{w}</li>{/each}</ul>{/if}
    </div>
  {/if}

  <div class="panel">
    <div class="panel-h"><h2>Ask the call</h2><span class="tag">RAG over filings/concalls</span></div>
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
      {:else if answer.hits?.length}
        <div class="muted" style="margin-top:0.6rem">Retrieved excerpts (no API key for synthesis):</div>
        {#each answer.hits as h}<div class="note">[{h.chunk}] {h.content?.slice(0, 160)}… (d={h.distance})</div>{/each}
      {/if}
    {/if}
  </div>
{/if}
