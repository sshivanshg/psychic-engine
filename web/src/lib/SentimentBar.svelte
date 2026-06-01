<script lang="ts">
  import { signed } from '$lib/format';

  // News-flow distribution: share of fetched headlines that are positive / neutral / negative.
  let { summary = null }: { summary?: any } = $props();
  const pos = $derived(summary?.pos_share_pct ?? 0);
  const neg = $derived(summary?.neg_share_pct ?? 0);
  const neu = $derived(Math.max(0, +(100 - pos - neg).toFixed(1)));
</script>

{#if summary}
  <div class="sbar" title="{pos}% positive · {neu}% neutral · {neg}% negative">
    {#if pos > 0}<span class="seg seg-pos" style="width:{pos}%"></span>{/if}
    {#if neu > 0}<span class="seg seg-neu" style="width:{neu}%"></span>{/if}
    {#if neg > 0}<span class="seg seg-neg" style="width:{neg}%"></span>{/if}
  </div>
  <div class="sbar-legend">
    <span class="good">{pos}% pos</span> · <span class="muted">{neu}% neutral</span> ·
    <span class="bad">{neg}% neg</span>
    <span class="muted"> — {summary.n_articles} headlines, mean {signed(summary.mean_polarity, 2)}</span>
  </div>
{:else}
  <span class="muted">no headlines fetched</span>
{/if}
