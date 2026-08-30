/**
 * Stock2 Mobile - Public Cloudflare Worker
 *
 * The mobile analysis center is intentionally public. Cloudflare's static
 * asset binding serves the exported HTML, scripts, styles, and stock data.
 */

export default {
  async fetch(request, env) {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method Not Allowed', {
        status: 405,
        headers: {
          Allow: 'GET, HEAD',
          'Cache-Control': 'no-store'
        }
      });
    }

    if (!env.ASSETS) {
      return new Response('Server Configuration Error: ASSETS binding is missing.', {
        status: 500,
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'no-store'
        }
      });
    }

    return env.ASSETS.fetch(request);
  }
};
