<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api } from '$lib/api';
  import { num, pct } from '$lib/format';

  // The six analyzers, in run order. Deterministic compute (no LLM) — the "thinking" is the
  // factual read each produces; the optional Claude synthesis sits on top.
  const AGENTS = [
    { name: 'risk', label: 'Risk' },
    { name: 'technical', label: 'Technical' },
    { name: 'fundamental', label: 'Fundamental' },
    { name: 'macro', label: 'Macro / Sector' },
    { name: 'sentiment', label: 'Sentiment' },
    { name: 'ownership', label: 'Ownership' }
  ];

  let horizon = $state('annual');
  let narrate = $state(true);
  let running = $state(false);
  let finished = $state(false);
  let status = $state('idle');
  let error = $state<string | null>(null);
  let elapsed = $state(0);

  let ctx = $state<any>(null);
  let agentState = $state<Record<string, any>>({});
  let cards = $state<any[]>([]);
  let narrations = $state<Record<string, any>>({});
  let narrationInfo = $state<any>(null);
  let narrationSummary = $state<any>(null);
  let feed = $state<{ t: string; type: string; text: string; cls: string }[]>([]);

  let es: EventSource | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  let t0 = 0;
  let tapeEl: HTMLDivElement;

  const COMPONENT_DIMS = ['risk', 'technical', 'fundamental', 'macro', 'sentiment', 'ownership'];
  const short = (s: string) => (s ?? '').replace('.NS', '');
  const compColor = (v: number) => (v >= 66 ? 'var(--bad)' : v >= 40 ? 'var(--warn)' : 'var(--good)');

  const stamp = () => {
    const s = (Date.now() - t0) / 1000;
    return s.toFixed(2).padStart(6, '0');
  };

  function pushFeed(type: string, text: string, cls = '') {
    feed = [...feed, { t: stamp(), type, text, cls }];
    queueMicrotask(() => tapeEl && (tapeEl.scrollTop = tapeEl.scrollHeight));
  }

  function reset() {
    error = null;
    ctx = null;
    cards = [];
    narrations = {};
    narrationInfo = null;
    narrationSummary = null;
    feed = [];
    agentState = Object.fromEntries(AGENTS.map((a) => [a.name, { status: 'pending' }]));
  }

  function cleanup() {
    es?.close();
    es = null;
    if (timer) { clearInterval(timer); timer = null; }
  }

  function run() {
    cleanup();
    reset();
    running = true;
    finished = false;
    status = 'running';
    t0 = Date.now();
    elapsed = 0;
    timer = setInterval(() => (elapsed = (Date.now() - t0) / 1000), 100);

    es = new EventSource(api.streamAnalyzeUrl({ horizon, narrate }));
    es.onmessage = (e) => handle(JSON.parse(e.data));
    es.addEventListener('end', () => endRun('done'));
    es.onerror = () => {
      if (finished) return;
      error = 'stream connection lost — is the API up? (uv run tradeos serve)';
      endRun('error');
    };
  }

  function endRun(why: 'done' | 'error') {
    finished = true;
    running = false;
    status = why;
    if (timer) { clearInterval(timer); timer = null; }
    es?.close();
    es = null;
  }

  function handle(ev: { type: string; payload: any }) {
    const { type, payload } = ev;
    switch (type) {
      case 'run_start':
        pushFeed('run_start', `as_of ${payload.as_of} · horizon ${payload.horizon} · narrate ${payload.narrate}`, 'ty');
        break;
      case 'context_loaded':
        ctx = payload;
        pushFeed('context', `loaded ${payload.n_holdings} holdings · ${payload.price_rows} price rows · ` +
          `${payload.n_fundamentals} fundamentals · ${payload.n_with_guidance} guidance · ` +
          `${payload.n_with_sentiment} sentiment · ${payload.n_with_ownership} ownership`, 'ty good');
        break;
      case 'agent_start':
        agentState = { ...agentState, [payload.agent]: { status: 'running' } };
        pushFeed('agent ▶', `${payload.agent} (${payload.scope}) running…`, 'ty');
        break;
      case 'agent_done':
        agentState = { ...agentState, [payload.agent]: { status: 'done', ...payload } };
        pushFeed('agent ✓', `${payload.agent} (${payload.latency_ms}ms) — ${payload.note}`, 'ty good');
        (payload.warnings ?? []).forEach((w: string) => pushFeed('⚠ warn', `${payload.agent}: ${w}`, 'ty warn'));
        break;
      case 'ranking':
        pushFeed('ranking', `by risk contribution → ${payload.order.map(short).join(' · ')}`, 'ty');
        break;
      case 'card':
        cards = [...cards, payload];
        pushFeed('card', `#${payload.rank} ${short(payload.symbol)} · attention ${num(payload.attention?.score, 0)} · ` +
          `confidence ${payload.confidence?.level ?? '—'}`, 'ty');
        break;
      case 'narration_start':
        narrationInfo = { count: payload.count, model: payload.model };
        pushFeed('LLM ▶', `synthesising ${payload.count} card(s) via ${payload.model}…`, 'ty');
        break;
      case 'narration_done':
        narrations = { ...narrations, [payload.symbol]: payload };
        pushFeed('LLM ✓', `${short(payload.symbol)} synthesised` +
          (payload.trace ? ` · ${payload.trace.output_tokens} out tok · ${num(payload.trace.latency_ms, 0)}ms` : ''), 'ty good');
        break;
      case 'narration_skipped':
        narrationInfo = { skipped: payload.reason };
        pushFeed('LLM —', payload.reason, 'ty warn');
        break;
      case 'narration_error':
        pushFeed('LLM ✗', `${short(payload.symbol)} synthesis failed`, 'ty bad');
        break;
      case 'narration_summary':
        narrationSummary = payload;
        break;
      case 'run_complete':
        pushFeed('done', `${payload.n_cards} cards · ${payload.n_narrated} narrated`, 'ty good');
        break;
      case 'error':
        error = payload.message;
        pushFeed('ERROR', payload.message, 'ty bad');
        break;
    }
  }

  onDestroy(cleanup);
</script>

<div class="topbar">
  <div>
    <h1>Reasoning monitor</h1>
    <div class="sub">watch the multi-agent run live — every agent's read, the per-holding synthesis, the raw event tape. Descriptive only.</div>
  </div>
</div>

<div class="run-bar">
  <div class="field" style="min-width:120px">
    <label for="hz">Horizon</label>
    <select id="hz" bind:value={horizon} disabled={running}>
      <option value="annual">annual</option>
      <option value="q">quarter</option>
      <option value="m">month</option>
      <option value="w">week</option>
      <option value="d">day</option>
    </select>
  </div>
  <label class="field-inline" style="padding-bottom:0.55rem">
    <input type="checkbox" bind:checked={narrate} disabled={running} style="width:auto" /> LLM synthesis
  </label>
  <button onclick={run} disabled={running}>{running ? 'Running…' : 'Run analysis'}</button>
  <div class="grow"></div>
  <span class="status-dot {status}"></span>
  <span class="muted mono">
    {#if status === 'running'}analysing… {elapsed.toFixed(1)}s
    {:else if status === 'done'}done in {elapsed.toFixed(1)}s
    {:else if status === 'error'}stopped
    {:else}press Run{/if}
  </span>
</div>

{#if error}<div class="err" style="margin-bottom:1rem">{error}</div>{/if}

<div class="monitor">
  <div>
    <!-- data load -->
    {#if ctx}
      <div class="panel">
        <div class="panel-h"><h2>1 · Point-in-time data load</h2><span class="hint">the run's provenance</span></div>
        <div class="chips">
          <span class="chip">as of <b>{ctx.as_of}</b></span>
          <span class="chip">benchmark <b>{short(ctx.benchmark)}</b></span>
          <span class="chip"><b>{ctx.n_holdings}</b> holdings</span>
          <span class="chip"><b>{ctx.price_rows}</b> price rows</span>
          <span class="chip"><b>{ctx.n_fundamentals}</b> w/ fundamentals</span>
          <span class="chip"><b>{ctx.n_with_guidance}</b> w/ guidance</span>
          <span class="chip"><b>{ctx.n_with_sentiment}</b> w/ sentiment</span>
          <span class="chip"><b>{ctx.n_with_ownership}</b> w/ ownership</span>
        </div>
      </div>
    {/if}

    <!-- agents -->
    <div class="panel">
      <div class="panel-h"><h2>2 · Analyzer agents</h2><span class="hint">6 pure-compute agents over one shared load</span></div>
      <div class="agents-grid">
        {#each AGENTS as a (a.name)}
          {@const s = agentState[a.name] ?? { status: 'pending' }}
          <div class="agent-tile {s.status}">
            <div class="ah">
              <span class="an"><span class="status-dot {s.status === 'running' ? 'running' : s.status === 'done' ? 'done' : s.status === 'error' ? 'error' : ''}"></span>{a.label}</span>
              <span class="al">{s.status === 'done' ? `${s.latency_ms}ms` : s.status}</span>
            </div>
            {#if s.note}<div class="anote">{s.note}</div>{/if}
            {#if s.metrics}
              <div class="chips">
                {#each Object.entries(s.metrics) as [k, v]}
                  {#if v != null && v !== ''}<span class="chip">{k} <b>{v}</b></span>{/if}
                {/each}
              </div>
            {/if}
            {#each (s.warnings ?? []) as w}<div class="awarn">⚠ {w}</div>{/each}
            {#if s.per_symbol}
              <details>
                <summary>per-holding</summary>
                <table>
                  <tbody>
                    {#each Object.entries(s.per_symbol) as [sym, d]}
                      <tr>
                        <td style="text-align:left">{short(sym)}</td>
                        {#each Object.values(d as Record<string, any>) as cell}
                          <td>{cell ?? '—'}</td>
                        {/each}
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </details>
            {/if}
          </div>
        {/each}
      </div>
    </div>

    <!-- per-holding reads -->
    {#if cards.length}
      <div class="panel">
        <div class="panel-h"><h2>3 · Per-holding reads</h2><span class="hint">ranked by risk contribution · attention decomposition + the LLM synthesis</span></div>
        <div class="cards">
          {#each cards as c (c.symbol)}
            {@const a = c.attention ?? {}}
            {@const comps = a.components ?? {}}
            {@const nar = narrations[c.symbol]}
            <div class="card" style="cursor:default">
              <div class="top">
                <div>
                  <div class="sym">{short(c.symbol)}</div>
                  <div class="sub">wt {pct(c.risk?.weight_pct)} · risk {pct(c.risk?.risk_contribution_pct)} · β {num(c.risk?.beta)}</div>
                </div>
                <span class="attn {a.score == null ? 'attn-none' : a.score >= 66 ? 'attn-hot' : a.score >= 40 ? 'attn-warm' : 'attn-calm'}">
                  {a.score == null ? '—' : Math.round(a.score)}
                </span>
              </div>

              <div style="margin-top:0.6rem">
                {#each COMPONENT_DIMS as dim}
                  {#if comps[dim] != null}
                    <div class="comp-row">
                      <span class="cl">{dim}</span>
                      <span class="comp-bar"><span style="width:{comps[dim]}%; background:{compColor(comps[dim])}"></span></span>
                      <span class="comp-v">{Math.round(comps[dim])}</span>
                    </div>
                  {/if}
                {/each}
              </div>

              {#if a.drivers?.length}<div class="drivers">{a.drivers.slice(0, 3).join(' · ')}</div>{/if}
              {#if c.confidence}<div class="conf">confidence {c.confidence.level} ({num(c.confidence.score)}) — {(c.confidence.reasons ?? []).join('; ')}</div>{/if}

              {#if nar?.card}
                <div class="synth">
                  <div class="lbl">LLM synthesis{#if nar.trace} · {nar.trace.output_tokens} tok · {num(nar.trace.latency_ms, 0)}ms{#if nar.trace.cost_usd} · ~${num(nar.trace.cost_usd, 4)}{/if}{/if}</div>
                  <p>{nar.card.synthesis}</p>
                  {#if nar.card.watch_items?.length}
                    <ul>{#each nar.card.watch_items as w}<li style="font-size:0.8rem">{w}</li>{/each}</ul>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>
        {#if narrationInfo?.skipped}
          <div class="note" style="margin-top:0.7rem">LLM synthesis off — {narrationInfo.skipped}. The deterministic agent reads above are complete.</div>
        {:else if narrationSummary}
          <div class="note" style="margin-top:0.7rem">
            LLM: {narrationSummary.calls} call(s) · {narrationSummary.input_tokens} in / {narrationSummary.output_tokens} out tokens · {num(narrationSummary.total_latency_ms, 0)}ms{#if narrationSummary.est_cost_usd} · est ~${num(narrationSummary.est_cost_usd, 4)}{/if}
          </div>
        {/if}
      </div>
    {/if}

    {#if !running && !ctx && !error}
      <div class="panel"><div class="note">Press <strong>Run analysis</strong> to watch the six agents work the book live — each one's read, the per-holding attention decomposition, and (with an API key) the streamed Claude synthesis.</div></div>
    {/if}
  </div>

  <!-- live event tape -->
  <div>
    <div class="panel-h"><h2 style="font-size:0.85rem">Live event tape</h2><span class="hint">{feed.length}</span></div>
    <div class="tape" bind:this={tapeEl}>
      {#if !feed.length}<div class="ln tt">— awaiting run —</div>{/if}
      {#each feed as f}
        <div class="ln"><span class="tt">{f.t}</span> <span class="ty {f.cls.replace('ty', '').trim()}">{f.type}</span> {f.text}</div>
      {/each}
    </div>
  </div>
</div>
