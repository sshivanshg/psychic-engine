// Pure SPA: render client-side and talk to the FastAPI backend. No SSR/prerender (the data is
// live, point-in-time, and personal — there's nothing to statically prerender).
export const ssr = false;
export const prerender = false;
