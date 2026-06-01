<script lang="ts">
  import '../app.css';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import { theme, toggleTheme } from '$lib/theme';

  let { children } = $props();
  let holdings = $state<any[]>([]);

  const nav = [
    { href: '/', label: 'Overview' },
    { href: '/news', label: 'Newsroom' },
    { href: '/runs', label: 'Briefs' },
    { href: '/coverage', label: 'Data Coverage' },
    { href: '/live', label: 'Reasoning Monitor' },
    { href: '/briefing', label: 'Briefing' },
    { href: '/manage', label: 'Manage' }
  ];
  const active = (href: string, path: string) => (href === '/' ? path === '/' : path.startsWith(href));

  // ───────── Cmd-K command palette: jump to any symbol's Workbench or any page ─────────
  let paletteOpen = $state(false);
  let pq = $state('');
  let sel = $state(0);
  let universe = $state<string[]>([]); // lazy-loaded symbol list (coverage = holdings ∪ universe)

  type Cmd = { label: string; href: string; kind: string };
  const commands = $derived<Cmd[]>([
    ...nav.map((n) => ({ label: n.label, href: n.href, kind: 'page' })),
    ...universe.map((s) => ({ label: s.replace('.NS', ''), href: `/analyst/${s}`, kind: 'symbol' }))
  ]);
  const hits = $derived(
    (pq ? commands.filter((c) => c.label.toLowerCase().includes(pq.toLowerCase())) : commands).slice(0, 40)
  );

  async function openPalette() {
    paletteOpen = true;
    pq = '';
    sel = 0;
    if (!universe.length) {
      try {
        universe = ((await api.coverage()).rows ?? []).map((r: any) => r.symbol);
      } catch {
        universe = holdings.map((h) => h.symbol);
      }
    }
  }

  function onKey(e: KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      paletteOpen ? (paletteOpen = false) : openPalette();
      return;
    }
    if (!paletteOpen) return;
    if (e.key === 'Escape') paletteOpen = false;
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, hits.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); }
    else if (e.key === 'Enter' && hits[sel]) { goto(hits[sel].href); paletteOpen = false; }
  }

  onMount(async () => {
    try {
      holdings = await api.holdings();
    } catch {
      holdings = [];
    }
  });
</script>

<svelte:window on:keydown={onKey} />

<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="logo">T</div>
      <div class="name">TradeOS<small>portfolio intelligence</small></div>
    </div>

    <nav class="nav">
      <div class="label">Dashboard</div>
      {#each nav as n}
        <a href={n.href} class:active={active(n.href, $page.url.pathname)}><span class="ic"></span>{n.label}</a>
      {/each}

      {#if holdings.length}
        <div class="label" style="margin-top:0.7rem">Holdings</div>
        {#each holdings as h}
          <a class="holding" href={`/analyst/${h.symbol}`}
             class:active={$page.url.pathname === `/analyst/${h.symbol}`}>
            <span class="ic"></span>{h.symbol.replace('.NS', '')}
          </a>
        {/each}
      {/if}
    </nav>

    <div style="margin-top:auto; display:flex; flex-direction:column; gap:0.7rem">
      <button class="theme-toggle" onclick={openPalette} title="Jump to… (⌘K)" aria-label="Command palette">
        <span class="ti">⌕</span><span>Jump to…</span><kbd class="kbd">⌘K</kbd>
      </button>
      <button class="theme-toggle" onclick={toggleTheme}
              title="Switch theme" aria-label="Switch theme">
        <span class="ti">{$theme === 'black' ? '☀' : '☾'}</span>
        <span>{$theme === 'black' ? 'Light theme' : 'Black theme'}</span>
      </button>
      <div class="note">Descriptive only — you make the call.</div>
    </div>
  </aside>

  <main class="content"><div class="wrap">{@render children()}</div></main>
</div>

{#if paletteOpen}
  <div
    class="palette-bg"
    role="button"
    tabindex="-1"
    aria-label="Close command palette"
    onclick={(e) => e.currentTarget === e.target && (paletteOpen = false)}
    onkeydown={(e) => e.key === 'Escape' && (paletteOpen = false)}
  >
    <div class="palette">
      <!-- svelte-ignore a11y_autofocus -->
      <input
        class="palette-in"
        placeholder="Jump to a page or a symbol's Analyst Workbench…"
        bind:value={pq}
        autofocus
        oninput={() => (sel = 0)}
      />
      <div class="palette-list">
        {#each hits as h, i}
          <a
            class="palette-row"
            class:sel={i === sel}
            href={h.href}
            onmouseenter={() => (sel = i)}
            onclick={() => (paletteOpen = false)}
          >
            <span class="palette-kind {h.kind}">{h.kind}</span>
            <span class="palette-label">{h.label}</span>
          </a>
        {/each}
        {#if !hits.length}<div class="note" style="padding:0.7rem">no match</div>{/if}
      </div>
      <div class="palette-foot"><kbd class="kbd">↑↓</kbd> move · <kbd class="kbd">↵</kbd> open · <kbd class="kbd">esc</kbd> close</div>
    </div>
  </div>
{/if}
