// Only handles alerts. Pages always come from the network, so nothing private is kept offline.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  // "Start this build?" carries two buttons. Pressing one makes the request the app's own button makes, signed in
  // as this browser, so the server still decides whether this person may. Tapping the alert itself just opens the idea.
  // A sign-off request gets one button, "Review the request", which opens the idea. Approving or sending back
  // stays in the app, where a note can go with it.
  const asks = d.ask === "build" && d.cardId;
  const review = d.ask === "review" && d.cardId;
  e.waitUntil(self.registration.showNotification(d.title || "Inrface", {
    body: d.body || "", icon: "/icons/icon-192.png", badge: "/icons/icon-192.png",
    tag: d.cardId || undefined, data: { cardId: d.cardId || null, ask: asks ? "build" : null },
    actions: asks ? [{ action: "build", title: "Start build" }, { action: "later", title: "Not now" }]
      : review ? [{ action: "review", title: "Review the request" }] : [],
  }));
});

// Answers the request from an alert button. Anything unexpected (login ended, offline, a refusal) opens the idea instead,
// so the person sees it and decides there.
async function answer(id, action, title) {
  const path = "/api/cards/" + encodeURIComponent(id) + (action === "build" ? "/build" : "/build-ask/decline");
  const say = (head, body) => self.registration.showNotification(head, { body, icon: "/icons/icon-192.png", badge: "/icons/icon-192.png", tag: id, data: { cardId: id, ask: null } });
  try {
    const r = await fetch(path, { method: "POST", credentials: "same-origin", redirect: "manual",
      headers: { "X-Requested-With": "Inrface", "Content-Type": "application/json" }, body: "{}" });
    const json = (r.headers.get("content-type") || "").includes("json");
    if (!r.ok || r.type === "opaqueredirect" || !json) throw new Error("not ok");
    const out = await r.json();
    if (action !== "build") return say("Left as it was", title);
    if (out.waiting_for_approval) return say("Sent for sign-off", title);
    const failed = (out.sent || []).filter((x) => !x.ok);
    if (failed.length) return say("Not everything went", title + ": open the idea to see where.");
    return say("Build started", title);
  } catch (_) {
    return say("Open Inrface to answer this", title);
  }
}
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const id = e.notification.data && e.notification.data.cardId;
  if (id && e.notification.data.ask === "build" && (e.action === "build" || e.action === "later")) {
    e.waitUntil(answer(id, e.action, e.notification.body));
    return;
  }
  const url = id ? "/#card=" + encodeURIComponent(id) : "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((all) => {
    for (const c of all) { c.navigate(url).catch(() => {}); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
