// The page where someone lets an AI app act for them. The answer goes to the app by fetch, and the browser is
// then sent on to the AI app's own address, which a plain form may not do under this site's rules.
(() => {
  const req = document.body.dataset.req, msg = document.getElementById("msg");
  const buttons = [...document.querySelectorAll("button")];
  async function answer(allow) {
    buttons.forEach((b) => { b.disabled = true; });
    msg.hidden = true;
    let again = true;
    try {
      const r = await fetch("/api/connect", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "Inrface" }, body: JSON.stringify({ req, allow }) });
      if (r.status === 401) { location.reload(); return; }
      const j = await r.json().catch(() => ({}));
      if (r.ok && typeof j.go === "string") { location.assign(j.go); return; }
      again = r.status !== 410;
      throw new Error(typeof j.detail === "string" ? j.detail : "That didn't work. Go back to your AI app and connect again.");
    } catch (e) {
      msg.textContent = e instanceof TypeError ? "Couldn't reach Inrface. Check your connection and try again." : e.message;
      msg.hidden = false;
      if (again) buttons.forEach((b) => { b.disabled = false; });
    }
  }
  document.getElementById("allow")?.addEventListener("click", () => answer(true));
  document.getElementById("deny")?.addEventListener("click", () => answer(false));
})();
