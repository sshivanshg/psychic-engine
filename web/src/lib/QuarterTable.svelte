<script lang="ts">
  import { pct } from '$lib/format';

  // The last-N quarters of reported results (revenue · net income · net margin), free & deterministic.
  let { rows = [] }: { rows?: any[] } = $props();

  // Reporting currency figures are large — show in ₹ crore (÷1e7) like an Indian results table.
  const cr = (v: number | null) =>
    v == null ? '—' : (v / 1e7).toLocaleString(undefined, { maximumFractionDigits: 0 });
</script>

{#if rows.length}
  <table>
    <thead>
      <tr><th>Quarter</th><th>Revenue (₹cr)</th><th>Net income (₹cr)</th><th>Net margin</th></tr>
    </thead>
    <tbody>
      {#each rows as r}
        <tr>
          <td>{r.q}</td>
          <td>{cr(r.revenue)}</td>
          <td>{cr(r.net_income)}</td>
          <td>{pct(r.net_margin_pct)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{:else}
  <span class="muted">no quarterly results on file</span>
{/if}
