<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  let rows = $state<any[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let q = $state('');

  onMount(async () => {
    try {
      rows = (await api.coverage()).rows ?? [];
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });

  const filtered = $derived(rows.filter((r) => !q || r.symbol.toLowerCase().includes(q.toLowerCase())));

  // a presence cell: green when fetched, faint dash when absent — the blind-spot map at a glance
  const cell = (present: unknown) => (present ? 'cov-yes' : 'cov-no');
</script>

<div class="topbar">
  <div>
    <h1>Data coverage</h1>
    <div class="sub">what's actually been fetched per name — the desk's blind-spot map. {rows.length} symbols.</div>
  </div>
</div>

{#if loading}
  <div class="loading">Scanning every table…</div>
{:else if error}
  <div class="err">{error}</div>
{:else}
  <div class="run-bar"><input style="min-width:220px" placeholder="filter symbol…" bind:value={q} /></div>
  <div class="panel" style="overflow-x:auto">
    <table class="cov">
      <thead>
        <tr>
          <th>Symbol</th><th>Prices</th><th>History</th><th>Quarters</th><th>Latest Q</th>
          <th>News</th><th>Latest news</th><th>Ownership</th><th>Docs</th><th>Latest doc</th>
        </tr>
      </thead>
      <tbody>
        {#each filtered as r}
          <tr>
            <td><a href={`/analyst/${r.symbol}`}>{r.symbol.replace('.NS', '')}</a></td>
            <td class={cell(r.price_rows)}>{r.price_rows || '—'}</td>
            <td class="muted">{r.price_start ? `${r.price_start} → ${r.price_end}` : '—'}</td>
            <td class={cell(r.quarters)}>{r.quarters || '—'}</td>
            <td>{r.latest_quarter ?? '—'}</td>
            <td class={cell(r.news)}>{r.news || '—'}</td>
            <td>{r.latest_news ?? '—'}</td>
            <td class={cell(r.ownership_at)}>{r.ownership_at ?? '—'}</td>
            <td class={cell(r.doc_chunks)}>{r.doc_chunks ? `${r.doc_sources} src / ${r.doc_chunks}` : '—'}</td>
            <td>{r.latest_doc_period ?? '—'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <footer>Defaults to your holdings; degrades to the declared universe when the book is empty. Click a symbol to open its Analyst Workbench.</footer>
{/if}
