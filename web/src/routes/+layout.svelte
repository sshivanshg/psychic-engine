<script lang="ts">
  import '../app.css';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import { theme, toggleTheme } from '$lib/theme';

  let { children } = $props();
  let holdings = $state<any[]>([]);

  onMount(async () => {
    try {
      holdings = await api.holdings();
    } catch {
      holdings = [];
    }
  });

  const nav = [
    { href: '/', label: 'Overview' },
    { href: '/live', label: 'Reasoning Monitor' },
    { href: '/briefing', label: 'Briefing' },
    { href: '/eval', label: 'Signal Eval' },
    { href: '/manage', label: 'Manage' }
  ];
  const active = (href: string, path: string) => (href === '/' ? path === '/' : path.startsWith(href));
</script>

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
          <a class="holding" href={`/stock/${h.symbol}`}
             class:active={$page.url.pathname === `/stock/${h.symbol}`}>
            <span class="ic"></span>{h.symbol.replace('.NS', '')}
          </a>
        {/each}
      {/if}
    </nav>

    <div style="margin-top:auto; display:flex; flex-direction:column; gap:0.7rem">
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
