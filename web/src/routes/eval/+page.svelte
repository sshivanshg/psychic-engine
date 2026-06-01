<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { num } from '$lib/format';
  import { theme } from '$lib/theme';
  import Chart from '$lib/Chart.svelte';
  import { icBarOption } from '$lib/charts';

  let data = $state<any>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);

  onMount(async () => {
    try {
      data = await api.evalSignals(21);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  });
</script>

<div class="topbar">
  <div>
    <h1>Signal eval</h1>
    <div class="sub">does each signal actually predict forward returns? point-in-time backtest, honest about power.</div>
  </div>
</div>

{#if loading}
  <div class="loading">Running the backtest…</div>
{:else if error}
  <div class="err">{error}</div>
{:else if data}
  <div class="kpis">
    <div class="kpi"><div class="k">Horizon</div><div class="v">{data.horizon_days}d</div><div class="x">forward return</div></div>
    <div class="kpi"><div class="k">Universe</div><div class="v">{data.universe}</div><div class="x">names</div></div>
    <div class="kpi"><div class="k">Signals</div><div class="v">{data.n_signals}</div><div class="x">tested</div></div>
    <div class="kpi"><div class="k">Cost</div><div class="v">{data.cost_bps}bp</div><div class="x">round-trip / leg</div></div>
    <div class="kpi"><div class="k">NW lag</div><div class="v">{data.nw_lag}d</div><div class="x">overlap-adjusted</div></div>
  </div>

  <div class="panel">
    <div class="panel-h"><h2>Information coefficient by signal</h2><span class="hint">amber = |t| ≥ 2 (significant) · blue = noise</span></div>
    {#key $theme}<Chart option={icBarOption(data.signals)} height="260px" />{/key}
  </div>

  <div class="panel">
    <div class="panel-h"><h2>Full metrics</h2></div>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr><th>Signal</th><th>Kind</th><th>Dates</th><th>IC</th><th>ICIR</th><th>t (NW)</th><th>Hit%</th><th>LS%</th><th>LS net%</th></tr>
        </thead>
        <tbody>
          {#each Object.entries(data.signals) as [name, s] (name)}
            {@const sig = s as any}
            <tr>
              <td>{name}</td>
              <td><span class="tag">{sig.kind}</span></td>
              <td>{sig.n_dates}</td>
              <td>{num(sig.ic, 3)}</td>
              <td>{num(sig.icir, 2)}</td>
              <td style:color={Math.abs(sig.t_stat ?? 0) >= 2 ? 'var(--warn)' : 'inherit'}>{num(sig.t_stat, 2)}</td>
              <td>{num(sig.hit_rate_pct, 1)}</td>
              <td>{num(sig.ls_spread_pct, 2)}</td>
              <td style:color={(sig.ls_spread_net_pct ?? 0) >= 0 ? 'var(--good)' : 'var(--bad)'}>{num(sig.ls_spread_net_pct, 2)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="note" style="margin-top:0.7rem">
      |t| ≳ 2 ≈ significant (Newey-West, overlap-adjusted). LS net% is after round-trip cost. Fundamental
      signals are announcement-lagged. {data.survivorship}. Multiple testing: {data.n_signals} signals scored,
      no deflation — a lone |t|&gt;2 is suspect. Small universe ⇒ illustrative, not conclusive.
    </div>
  </div>
{/if}
