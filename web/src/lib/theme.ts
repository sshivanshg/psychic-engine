// Theme store — a true-black default ('black') and a 'light' option, persisted to localStorage and
// applied as `data-theme` on <html>. The CSS variables in app.css key off that attribute; ECharts
// charts read the same variables live (see charts.ts) and re-mount on toggle ({#key $theme} in pages).
import { writable } from 'svelte/store';

export type Theme = 'black' | 'light';
const KEY = 'tradeos-theme';

function initial(): Theme {
  if (typeof localStorage !== 'undefined') {
    const t = localStorage.getItem(KEY);
    if (t === 'black' || t === 'light') return t;
  }
  return 'black';
}

export const theme = writable<Theme>(initial());

theme.subscribe((t) => {
  if (typeof document !== 'undefined') document.documentElement.dataset.theme = t;
  if (typeof localStorage !== 'undefined') localStorage.setItem(KEY, t);
});

export function toggleTheme(): void {
  theme.update((t) => (t === 'black' ? 'light' : 'black'));
}
