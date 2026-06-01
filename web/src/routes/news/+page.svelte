<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import NewsFeed from '$lib/NewsFeed.svelte';

  let all = $state<any[]>([]);
  let asOf = $state<string | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // filters
  let q = $state('');
  let sym = $state('');
  let sentiment = $state('all'); // all | pos | neg | neutral
  let catalystsOnly = $state(false);

  onMount(async () => {
    try {
      const r = await api.news(400);
      all = r.headlines ?? [];
      asOf = r.as_of;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });

  const symbols = $derived([...new Set(all.map((h) => h.symbol))].sort());

  const filtered = $derived(
    all.filter((h) => {
      if (sym && h.symbol !== sym) return false;
      if (catalystsOnly && !h.event) return false;
      if (sentiment === 'pos' && !(h.polarity > 0)) return false;
      if (sentiment === 'neg' && !(h.polarity < 0)) return false;
      if (sentiment === 'neutral' && !(h.polarity === 0 || h.polarity == null)) return false;
      if (q && !(h.title ?? '').toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    })
  );

  const posN = $derived(filtered.filter((h) => h.polarity > 0).length);
  const negN = $derived(filtered.filter((h) => h.polarity < 0).length);
  const catN = $derived(filtered.filter((h) => h.event).length);
</script>

<div class="topbar">
  <div>
    <h1>Newsroom</h1>
    <div class="sub">every fetched headline across the book{#if asOf} · as of {asOf}{/if} · snapshot · eval-barred</div>
  </div>
</div>

{#if loading}
  <div class="loading">Loading the wire…</div>
{:else if error}
  <div class="err">{error}</div>
{:else}
  <div class="run-bar">
    <input style="flex:1; min-width:200px" placeholder="search headlines…" bind:value={q} />
    <select bind:value={sym}>
      <option value="">all symbols</option>
      {#each symbols as s}<option value={s}>{s.replace('.NS', '')}</option>{/each}
    </select>
    <select bind:value={sentiment}>
      <option value="all">all sentiment</option>
      <option value="pos">positive</option>
      <option value="neg">negative</option>
      <option value="neutral">neutral</option>
    </select>
    <label class="field-inline"><input type="checkbox" bind:checked={catalystsOnly} style="width:auto" /> catalysts only</label>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="k">Headlines</div><div class="v">{filtered.length}</div><div class="x">of {all.length} fetched</div></div>
    <div class="kpi good"><div class="k">Positive</div><div class="v">{posN}</div></div>
    <div class="kpi bad"><div class="k">Negative</div><div class="v">{negN}</div></div>
    <div class="kpi warn"><div class="k">Catalysts</div><div class="v">{catN}</div><div class="x">auto-tagged</div></div>
  </div>

  <div class="panel">
    <NewsFeed headlines={filtered} showSymbol={true} />
  </div>

  <footer>News is a current snapshot, lexicon-scored — descriptive, not point-in-time; barred from the eval harness.</footer>
{/if}
