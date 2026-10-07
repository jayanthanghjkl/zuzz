const CACHE = "zuzz-v1";
const ASSETS = [
  "/",
  "/static/css/main.css",
  "/static/css/global.css",
  "/static/css/dashboard.css",
  "/static/js/app.js",
  "/static/js/dashboard.js",
  "/static/js/settings.js",
  "/static/js/plugins.js",
  "/static/js/routines.js",
  "/static/js/activity.js",
  "/static/js/brain_viz.js",
  "/pages/dashboard.html",
  "/pages/settings.html",
  "/pages/plugins.html",
  "/pages/routines.html",
  "/pages/activity.html",
  "/pages/brain_viz.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const clone = networkResponse.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, clone));
        }
        return networkResponse;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
