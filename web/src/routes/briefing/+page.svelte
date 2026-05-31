<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { num, pct } from '$lib/format';

  let data = $state<any>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);

  onMount(async () => {
    try {
      data = await api.briefing();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });
</script>

<div class="topbar">
  <div>
    <h1>Pre-market briefing</h1>
    {#if data}<div class="sub">as of {data.as_of} · horizon {data.horizon} · alerts on your rules</div>{/if}
  </div>
</div>

{#if loading}
  <div class="loading">Building the briefing…</div>
{:else if error}
  <div class="err">{error}</div>
{:else if data}
  {@const r = data.risk_overview}
  {@const s = data.sector_overview}
  <div class="kpis">
    <div class="kpi"><div class="k">Book vol</div><div class="v">{pct(r.vol_pct)}</div><div class="x">at {data.horizon}</div></div>
    <div class="kpi"><div class="k">Beta</div><div class="v">{num(r.beta)}</div></div>
    <div class="kpi warn"><div class="k">Top risk</div><div class="v">{pct(r.top_risk_pct)}</div><div class="x">{(r.top_risk_contributor ?? '—').replace('.NS','')}</div></div>
    {#if s?.top_sector}<div class="kpi"><div class="k">Top sector</div><div class="v">{pct(s.top_sector_pct)}</div><div class="x">{s.top_sector}</div></div>{/if}
    <div class="kpi" class:bad={data.n_flagged > 0}><div class="k">Flagged</div><div class="v">{data.n_flagged}/{data.stocks.length}</div><div class="x">holdings</div></div>
  </div>

  {#if data.n_flagged === 0}
    <div class="panel">✓ Nothing tripped your alert rules today.</div>
  {:else}
    {#each data.flagged as stock}
      <div class="panel">
        <div class="panel-h">
          <a class="sym" href={`/stock/${stock.symbol}`} style="font-weight:700">{stock.symbol.replace('.NS', '')}</a>
          <span class="muted mono">attention {num(stock.attention, 0)} · confidence {stock.confidence ?? '—'}</span>
        </div>
        <ul>{#each stock.alerts as a}<li class="alert">{a}</li>{/each}</ul>
      </div>
    {/each}
  {/if}
  <div class="note">Descriptive flags on your rules — you make the call.</div>
{/if}
