// Only handles alerts. Pages always come from the network, so nothing private is kept offline.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Inrface", {
    body: d.body || "", icon: "/icons/icon-192.png", badge: "/icons/icon-192.png",
    tag: d.cardId || undefined, data: { cardId: d.cardId || null },
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const id = e.notification.data && e.notification.data.cardId;
  const url = id ? "/#card=" + encodeURIComponent(id) : "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((all) => {
    for (const c of all) { c.navigate(url).catch(() => {}); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
