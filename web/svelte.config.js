import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // SPA build → emits web/build, which FastAPI serves in production (see api.py).
    adapter: adapter({ fallback: 'index.html' })
  }
};

export default config;
