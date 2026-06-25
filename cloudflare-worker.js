export default {
  async fetch(request) {
    const url = new URL(request.url);
    const telegramUrl = "https://api.telegram.org" + url.pathname + url.search;
    const headers = new Headers(request.headers);

    headers.delete("host");
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");
    headers.delete("x-forwarded-proto");
    headers.delete("x-real-ip");

    return fetch(new Request(telegramUrl, {
      method: request.method,
      headers,
      body: request.body,
    }));
  },
};
