<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';

  // ---- data ----
  let holdings = $state<any[]>([]);
  let coverage = $state<any[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // ---- add-holding form ----
  let addSym = $state('');
  let addQty = $state<number | null>(null);
  let addCost = $state<number | null>(null);
  let addFetch = $state(true);
  let adding = $state(false);
  let addMsg = $state<string | null>(null);
  let addErr = $state<string | null>(null);

  // ---- remove ----
  let removing = $state<string | null>(null);

  // ---- upload form ----
  let upSym = $state('');
  let upFile = $state<File | null>(null);
  let upFileName = $state('');
  let upPeriod = $state('');
  let upFiling = $state('');
  let upUrl = $state('');
  let uploading = $state(false);
  let upMsg = $state<string | null>(null);
  let upErr = $state<string | null>(null);

  // ---- ingest ----
  let ingesting = $state(false);
  let ingestMsg = $state<string | null>(null);
  let ingestErr = $state<string | null>(null);

  async function loadAll() {
    loading = true;
    error = null;
    try {
      holdings = await api.holdings();
      coverage = await api.docsStatus().catch(() => []);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }
  onMount(loadAll);

  async function addHolding() {
    addMsg = addErr = null;
    const symbol = addSym.trim().toUpperCase();
    if (!symbol || addQty == null) {
      addErr = 'Symbol and quantity are required.';
      return;
    }
    adding = true;
    try {
      const res = await api.addHolding({ symbol, quantity: addQty, avg_cost: addCost, fetch: addFetch });
      holdings = res.holdings;
      addMsg = res.warning ?? `Added ${symbol}${addFetch ? ' and fetched its price history.' : '.'}`;
      addSym = '';
      addQty = addCost = null;
      coverage = await api.docsStatus().catch(() => coverage);
    } catch (e) {
      addErr = (e as Error).message;
    } finally {
      adding = false;
    }
  }

  async function removeHolding(symbol: string) {
    removing = symbol;
    try {
      const res = await api.removeHolding(symbol);
      holdings = res.holdings;
      coverage = coverage.filter((c) => c.symbol !== symbol);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      removing = null;
    }
  }

  function pickFile(e: Event) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0] ?? null;
    upFile = f;
    upFileName = f?.name ?? '';
  }

  async function upload() {
    upMsg = upErr = null;
    const symbol = upSym.trim().toUpperCase();
    if (!symbol) { upErr = 'Pick a symbol.'; return; }
    if (!upFile) { upErr = 'Choose a PDF / txt / md file.'; return; }
    uploading = true;
    try {
      const res = await api.uploadDoc({
        symbol, file: upFile,
        period: upPeriod || undefined,
        filing_date: upFiling || undefined,
        source_url: upUrl || undefined
      });
      upMsg = res.stored
        ? `Stored ${res.chunks} chunks from ${res.source}${res.period ? ` (period ${res.period})` : ' — no period tag, won’t count for freshness'}.`
        : (res.note ?? 'No text could be extracted.');
      upFile = null;
      upFileName = upPeriod = upFiling = upUrl = '';
      coverage = await api.docsStatus().catch(() => coverage);
    } catch (e) {
      upErr = (e as Error).message;
    } finally {
      uploading = false;
    }
  }

  async function refreshData() {
    ingestMsg = ingestErr = null;
    ingesting = true;
    try {
      await api.ingest();
      ingestMsg = 'Price & fundamentals data refreshed.';
      coverage = await api.docsStatus().catch(() => coverage);
    } catch (e) {
      ingestErr = (e as Error).message;
    } finally {
      ingesting = false;
    }
  }

  const short = (s: string) => s.replace('.NS', '');
  const flagClass = (f: string) =>
    f === 'OK' ? 'flag-ok'
      : f === 'MISSING' || f === 'STALE' ? 'flag-bad'
      : f === 'UNTAGGED' ? 'flag-warn'
      : 'flag-muted';
</script>

<div class="topbar">
  <div>
    <h1>Manage</h1>
    <div class="sub">add holdings · upload quarterly results &amp; transcripts · refresh data</div>
  </div>
</div>

{#if error}<div class="err">{error}</div>{/if}

<!-- ---------------- Holdings ---------------- -->
<div class="panel">
  <div class="panel-h"><h2>Holdings</h2><span class="hint">{holdings.length} in your book</span></div>

  {#if loading}
    <div class="loading">Loading…</div>
  {:else}
    {#if holdings.length}
      <div style="overflow-x:auto">
        <table>
          <thead><tr><th>Symbol</th><th>Quantity</th><th>Avg cost</th><th></th></tr></thead>
          <tbody>
            {#each holdings as h (h.symbol)}
              <tr>
                <td>{short(h.symbol)}</td>
                <td>{h.quantity}</td>
                <td>{h.avg_cost ?? '—'}</td>
                <td>
                  <button class="btn-danger" disabled={removing === h.symbol}
                          onclick={() => removeHolding(h.symbol)}>
                    {removing === h.symbol ? '…' : 'Remove'}
                  </button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <div class="note">No holdings yet — add your first below.</div>
    {/if}

    <div class="formgrid" style="margin-top:1rem">
      <div class="field">
        <label for="add-sym">Symbol</label>
        <input id="add-sym" placeholder="RELIANCE.NS" bind:value={addSym}
               onkeydown={(e) => e.key === 'Enter' && addHolding()} />
      </div>
      <div class="field">
        <label for="add-qty">Quantity</label>
        <input id="add-qty" type="number" step="any" placeholder="10" bind:value={addQty} />
      </div>
      <div class="field">
        <label for="add-cost">Avg cost (optional)</label>
        <input id="add-cost" type="number" step="any" placeholder="2400" bind:value={addCost} />
      </div>
      <label class="field-inline" style="padding-bottom:0.55rem">
        <input type="checkbox" bind:checked={addFetch} style="width:auto" /> fetch price now
      </label>
      <button onclick={addHolding} disabled={adding}>{adding ? 'Adding…' : 'Add holding'}</button>
    </div>
    {#if addErr}<div class="note" style="color:var(--bad); margin-top:0.5rem">{addErr}</div>{/if}
    {#if addMsg}<div class="ok-msg">{addMsg}</div>{/if}
  {/if}
</div>

<!-- ---------------- Upload quarterly result ---------------- -->
<div class="panel">
  <div class="panel-h">
    <h2>Upload quarterly result / transcript</h2>
    <span class="hint">PDF · txt · md — feeds “Ask the call” &amp; the Fundamental agent</span>
  </div>

  <div class="formgrid">
    <div class="field">
      <label for="up-sym">Symbol</label>
      <input id="up-sym" list="hold-list" placeholder="INFY.NS" bind:value={upSym} />
      <datalist id="hold-list">
        {#each holdings as h}<option value={h.symbol}>{short(h.symbol)}</option>{/each}
      </datalist>
    </div>
    <div class="field" style="grid-column:span 2">
      <label for="up-file">File</label>
      <input id="up-file" type="file" accept=".pdf,.txt,.md" onchange={pickFile} />
    </div>
    <div class="field">
      <label for="up-period">Quarter-end</label>
      <input id="up-period" type="date" bind:value={upPeriod} />
    </div>
    <div class="field">
      <label for="up-filing">Filing date (optional)</label>
      <input id="up-filing" type="date" bind:value={upFiling} />
    </div>
  </div>
  <div class="formgrid" style="margin-top:0.7rem">
    <div class="field" style="grid-column:span 3">
      <label for="up-url">Source URL (optional)</label>
      <input id="up-url" placeholder="https://…" bind:value={upUrl} />
    </div>
    <button onclick={upload} disabled={uploading}>{uploading ? 'Uploading…' : 'Upload'}</button>
  </div>
  <div class="note" style="margin-top:0.6rem">
    Tag the <strong>quarter-end</strong> so the coverage check below knows what this document covers.
    Without it the file is still searchable but won’t count toward freshness.
  </div>
  {#if upErr}<div class="note" style="color:var(--bad); margin-top:0.4rem">{upErr}</div>{/if}
  {#if upMsg}<div class="ok-msg">{upMsg}</div>{/if}
</div>

<!-- ---------------- Document coverage ---------------- -->
<div class="panel">
  <div class="panel-h">
    <h2>Document coverage</h2>
    <span class="hint">does each holding’s latest reported quarter have a transcript?</span>
  </div>
  {#if coverage.length}
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr><th>Symbol</th><th>Status</th><th>Latest results</th><th>Latest transcript</th><th>Docs</th><th>Last ingested</th></tr>
        </thead>
        <tbody>
          {#each coverage as c (c.symbol)}
            <tr>
              <td>{short(c.symbol)}</td>
              <td><span class="flag {flagClass(c.flag)}">{c.flag}</span></td>
              <td>{c.latest_results ?? '—'}</td>
              <td>{c.latest_transcript ?? '—'}</td>
              <td>{c.docs}</td>
              <td>{c.last_ingested ? String(c.last_ingested).slice(0, 10) : '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="note" style="margin-top:0.6rem">
      <span class="flag flag-bad">MISSING</span> / <span class="flag flag-bad">STALE</span> = upload the latest results above ·
      <span class="flag flag-warn">UNTAGGED</span> = re-upload with a quarter-end ·
      <span class="flag flag-muted">UNCHECKED</span> = no fundamentals to compare yet.
    </div>
  {:else if !loading}
    <div class="note">No coverage data — add a holding and refresh.</div>
  {/if}
</div>

<!-- ---------------- Data refresh ---------------- -->
<div class="panel">
  <div class="panel-h"><h2>Data</h2><span class="hint">pull fresh prices &amp; fundamentals (yfinance)</span></div>
  <div class="row">
    <button class="btn-ghost" onclick={refreshData} disabled={ingesting}>
      {ingesting ? 'Refreshing… (this can take a minute)' : 'Refresh all data'}
    </button>
    {#if ingestMsg}<span class="ok-msg" style="margin:0">{ingestMsg}</span>{/if}
    {#if ingestErr}<span class="note" style="color:var(--bad); margin:0">{ingestErr}</span>{/if}
  </div>
</div>
