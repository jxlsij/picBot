export default {
  async fetch(request) {
    const url = new URL(request.url);
    const telegramUrl = "https://api.telegram.org" + url.pathname + url.search;

    return fetch(new Request(telegramUrl, {
      method: request.method,
      headers: request.headers,
      body: request.body,
    }));
  },
};
