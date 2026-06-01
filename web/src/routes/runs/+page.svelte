<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let runs = $state<any[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      const r = await api.analystRuns(80);
      runs = r.runs ?? [];
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });

  const totalCost = $derived(runs.reduce((a, r) => a + (r.cost_usd ?? 0), 0));
</script>

<div class="crumb"><a href="/">← Portfolio</a></div>
<h1>Analyst briefs</h1>
<div class="muted" style="margin-bottom:1rem">
  Every AI verdict you've run, newest first — across all names. Each is journaled (the brief as it was,
  no re-fetch). Click a name for its full per-stock history.
</div>

{#if loading}
  <div class="loading">Loading past briefs…</div>
{:else if error}
  <div class="err">{error}
    <div class="note" style="margin-top:0.5rem">Is the API up? <code>uv run tradeos serve</code></div>
  </div>
{:else if runs.length}
  <div class="panel">
    <div class="panel-h"><h2>{runs.length} briefs</h2><span class="hint">total spend ~${totalCost.toFixed(3)}</span></div>
    <table>
      <thead><tr><th>When</th><th>Name</th><th>Verdict</th><th>Model</th><th>Cost</th></tr></thead>
      <tbody>
        {#each runs as r}
          <tr>
            <td class="mono nowrap">{(r.run_at ?? '').slice(0, 16).replace('T', ' ')}</td>
            <td><a href={`/analyst/${r.symbol}`}>{r.symbol.replace('.NS', '')}</a></td>
            <td>{r.one_line}</td>
            <td class="muted">{r.model}</td>
            <td class="mono nowrap">${r.cost_usd ?? '—'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{:else}
  <div class="panel">
    <span class="muted">No briefs yet. Open any stock's Analyst page and run an AI verdict — each one is
    saved and listed here, so you can scan how reads evolved across your whole watchlist.</span>
  </div>
{/if}

<style>
  .nowrap { white-space: nowrap; }
</style>
