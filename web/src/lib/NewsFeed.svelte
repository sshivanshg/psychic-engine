<script lang="ts">
  import { signed } from '$lib/format';

  // The raw fetched headlines — each with its catalyst tag, polarity, publisher and date.
  // Reused by the Analyst Workbench (one name) and the Newsroom (whole book, showSymbol=true).
  let { headlines = [], showSymbol = false, max = 0 }: {
    headlines?: any[];
    showSymbol?: boolean;
    max?: number;
  } = $props();
  const rows = $derived(max ? headlines.slice(0, max) : headlines);

  const polClass = (p: number | null) =>
    p == null || p === 0 ? 'pol-zero' : p > 0 ? 'pol-pos' : 'pol-neg';
  // Colour the catalyst tag by family (legal=bad, results/deal=accent, etc.).
  const evtFamily = (e: string | null) => (e ? e.split('/')[0] : '');
</script>

<div class="newsfeed">
  {#each rows as h}
    <div class="news-row">
      <div class="news-main">
        {#if h.event}<span class="evt evt-{evtFamily(h.event)}">{h.event}</span>{/if}
        {#if showSymbol && h.symbol}
          <a class="news-sym" href={`/analyst/${h.symbol}`}>{h.symbol.replace('.NS', '')}</a>
        {/if}
        <span class="news-title">{h.title}</span>
      </div>
      <div class="news-meta">
        <span class="pol {polClass(h.polarity)}">{h.polarity == null ? '—' : signed(h.polarity, 2)}</span>
        {#if h.publisher}<span class="news-pub">{h.publisher}</span>{/if}
        <span class="news-date">{h.published ?? '—'}</span>
      </div>
    </div>
  {/each}
  {#if !rows.length}<div class="note">no headlines fetched</div>{/if}
</div>
