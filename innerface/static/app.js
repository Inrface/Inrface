"use strict";
const $app = document.getElementById("app");
const state = { me: null, tab: "talk", projectId: null, cards: [], chat: [], cardId: null, card: null, cardChat: [], missed: [], busy: false, draft: "", drafts: {}, offline: false, asGiver: false, github: {}, flow: {}, settingsTab: "you", settingsProject: null, open: {}, sources: {}, connectors: {}, connEdit: null, reveal: null, newKey: null, tools: null, usage: null, usageDays: 30, view: "list", dragId: null, dups: [], dupsOpen: false, shipped: null, share: {}, newShare: null, people: {}, invited: null, account: null, approvals: {}, runner: {}, runnerKey: null, home: false, missedOpen: {} };

// Text people wrote can be in any language. Where it runs from, left or right, follows what it says, so Arabic and Hebrew
// read properly beside English. Addresses and passwords stay left to right.
const OWN_DIRECTION = new Set(["input", "textarea", "p", "li", "h1", "h2", "h3", "h4", "blockquote", "summary"]);
const KEEP_LTR = new Set(["url", "email", "password", "tel", "number"]);

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  if ((OWN_DIRECTION.has(tag) || (tag === "button" && /\btitle\b/.test((props && props.class) || ""))) && !(props && "dir" in props) && !KEEP_LTR.has(props && props.type)) e.setAttribute("dir", "auto");
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
  return e;
}

let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, Math.max(3800, msg.length * 60));
}

class ApiError extends Error { constructor(m, status) { super(m); this.status = status; } }

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Requested-With": "Inrface" }, credentials: "same-origin" };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  let r;
  try { r = await fetch(path, init); }
  catch (_) { throw new ApiError("Couldn't reach the app. Your login may have expired, or you're offline.", 0); }
  // With built-in sign-in, the app says where to go when the session has ended.
  if (r.status === 401 && r.headers.get("X-Sign-In")) { location.assign("/signin"); throw new ApiError("Taking you to sign in.", 401); }
  const type = r.headers.get("content-type") || "";
  if (r.redirected || (!type.includes("json") && !type.includes("text/plain"))) throw new ApiError("Your login has expired.", 401);
  if (!r.ok) {
    let d = "That didn't work. Try again.";
    try { const j = await r.json(); if (typeof j.detail === "string") d = j.detail; } catch (_) {}
    throw new ApiError(d, r.status);
  }
  return type.includes("json") ? r.json() : r.text();
}

const project = (id) => state.me.projects.find((p) => p.id === id);
const receiverAnywhere = () => state.me.projects.some((p) => p.role === "receiver");
// A receiver can preview what givers see. While previewing, nothing they press changes anything.
const receives = (projectId) => !state.asGiver && project(projectId)?.role === "receiver";
const multiProject = () => state.me.projects.length > 1;
const talkProject = () => project(state.projectId) || state.me.projects[0];
const listNames = (names) => names.length <= 1 ? (names[0] || "") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
const receiversOf = (projectId) => listNames(project(projectId)?.receivers || []) || "the team";

// Which list a card sits in depends on who's looking: what needs you comes first.
function bucket(c) {
  const mine = receives(c.project_id);
  if (mine && c.build_ask) return "needs";
  if (c.status === "Needs info") return mine ? "waiting" : "needs";
  if (c.status === "New" || c.status === "Answered") return mine ? "needs" : "waiting";
  return c.status;
}
const GROUP_ORDER = ["needs", "waiting", "In dev for testing", "Immediate roadmap", "6-month roadmap", "Far down the line", "Done / Live", "Rejected"];
function groupTitle(key) {
  if (key === "needs") return state.asGiver || !receiverAnywhere() ? "Needs your answer" : "Needs you";
  if (key === "waiting") return state.asGiver || !receiverAnywhere() ? "Being reviewed" : "Waiting on others";
  return key;
}
// The labels an idea can be filed under by hand: the ones that count as roadmap, someday, in production or rejected.
const FILEABLE = ["Roadmap", "Someday", "In production", "Rejected"];
const fileLabels = (c) => projectLabels(c.project_id).filter((l) => FILEABLE.includes(l.kind));
const statusClass = (s) => String(s).replace(/[^A-Za-z0-9]/g, "");
const fmtDate = (s) => new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const lines = (v) => String(v || "").split("\n").map((x) => x.trim()).filter(Boolean);

// With more than one project, the list and the board show one at a time. Cards from two projects in one view would sit
// in columns that only one of them has.
const shownCards = () => multiProject() ? state.cards.filter((c) => c.project_id === talkProject().id) : state.cards;

function waitingCount() {
  return (state.home ? state.cards : shownCards()).filter((c) => bucket(c) === "needs").length;
}

// ---------- rendering ----------

// Where the screen was scrolled to (the main area scrolls, not the page), so tapping a button on a long screen doesn't throw the person back to the top.
// A different screen starts at the top.
let lastScreen = "";

function render() {
  const screen = [state.tab, state.cardId, state.settingsTab, state.settingsProject].join("|");
  const old = $app.querySelector("main");
  const keep = screen === lastScreen && old ? old.scrollTop : 0;
  lastScreen = screen;
  $app.classList.toggle("wide", state.tab === "cards" && state.view === "board" && !state.cardId);
  const onHome = state.home && multiProject() && !state.cardId && (state.tab === "talk" || state.tab === "cards");
  const main = state.cardId ? renderDetail() : onHome ? renderHome() : state.tab === "talk" ? renderTalk() : state.tab === "settings" ? renderSettings() : state.tab === "account" ? renderAccount() : renderCards();
  const count = waitingCount();
  $app.replaceChildren(...[
    h("header", null,
      state.cardId ? h("button", { class: "link", onclick: closeCard }, "‹ Ideas") : h("h1", { class: "brand" },
        h("img", { class: "mark", src: "/brand/mark.png", alt: "" }),
        h("img", { class: "word light", src: "/brand/wordmark.png", alt: "Inrface" }),
        h("img", { class: "word dark", src: "/brand/wordmark-white.png", alt: "" })),
      !state.cardId && (receiverAnywhere() || state.me.sign_in === "builtin")
        ? h("div", { class: "row" },
            !receiverAnywhere() || onHome || state.tab === "settings" || state.tab === "account" ? null : h("button", { class: "link", onclick: () => { state.asGiver = !state.asGiver; render(); } }, state.asGiver ? "Your view" : "Giver view"),
            receiverAnywhere() ? h("button", { class: "link", "aria-pressed": String(state.tab === "settings"), onclick: openSettings }, "Settings") : null,
            // Givers have no Settings screen, so this is its own place. It only exists where the app does its own sign-in.
            state.me.sign_in === "builtin" ? h("button", { class: "link", "aria-pressed": String(state.tab === "account"), onclick: openAccount }, "Account") : null)
        : null),
    state.cardId || onHome ? null : h("nav", { role: "tablist" },
      h("button", { role: "tab", "aria-selected": String(state.tab === "talk"), onclick: () => setTab("talk") }, "New idea"),
      h("button", { role: "tab", "aria-selected": String(state.tab === "cards"), onclick: () => setTab("cards") }, "Ideas", count ? h("span", { class: "badge" }, String(count)) : null)),
    onHome || state.cardId || state.tab === "settings" || state.tab === "account" ? null : projectBar(),
    main].filter(Boolean));
  if (keep) { const fresh = $app.querySelector("main"); if (fresh) fresh.scrollTop = keep; }
  const chat = $app.querySelector(".chat");
  if (chat) chat.scrollTop = chat.scrollHeight;
}

function chatInput(value, placeholder, oninput, onsend) {
  const ta = h("textarea", { rows: "4", placeholder, value, "aria-label": placeholder, oninput: (e) => oninput(e.target.value) });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onsend(); } });
  return ta;
}

// With more than one project, the app opens on a list of them. Each project is its own place: its own ideas, board,
// shipped list, tools and usage. Nothing from one shows in another.
function renderHome() {
  const tile = (p) => {
    const mine = state.cards.filter((c) => c.project_id === p.id);
    const waiting = mine.filter((c) => bucket(c) === "needs").length;
    const building = mine.filter((c) => c.status === "In dev for testing").length;
    const live = mine.filter((c) => c.status === "Done / Live").length;
    const facts = [waiting ? waiting + " waiting on you" : null, building ? building + " being built" : null, live ? live + " live" : null].filter(Boolean);
    return h("section", { class: "card projtile", "data-project": p.id },
      h("div", { class: "row between" },
        h("h2", null, p.name),
        h("span", { class: "chip" }, p.role === "receiver" ? "You review ideas" : "You send ideas")),
      h("p", { class: "hint" }, mine.length ? plural(mine.length, "idea") + (facts.length ? ": " + facts.join(", ") : "") : "No ideas yet"),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => openProject(p.id, "cards") }, "Open"),
        h("button", { class: "quiet", onclick: () => openProject(p.id, "talk") }, "New idea")));
  };
  return h("main", null, missedBlock(), pushBanner(), h("h2", { class: "homehead" }, "Your projects"), state.me.projects.map(tile));
}

function openProject(id, tab) {
  state.home = false; state.tab = tab; state.cardId = null; state.asGiver = false;
  state.projectId = id;
  try { localStorage.setItem("innerface.project", id); } catch (_) {}
  location.hash = "";
  if (state.view === "shipped") loadShipped();
  render();
  api("/api/chat?project_id=" + encodeURIComponent(id)).then((r) => { state.chat = r.messages; render(); }, () => {});
  if (tab === "cards") { refresh(); loadDups(); }
}

const projectBar = () => !multiProject() || state.home ? null : h("div", { class: "projbar" },
  h("button", { class: "link", onclick: () => { state.home = true; state.cardId = null; location.hash = ""; render(); } }, "‹ All projects"),
  h("strong", null, talkProject().name));

function renderTalk() {
  const ai = state.me.ai;
  const empty = !state.chat.length;
  const hasText = !!state.draft.trim();
  const p = talkProject();
  return h("main", { class: "talk" },
    h("div", { class: "chat", "aria-live": "polite" },
      empty ? h("div", { class: "intro" },
        h("strong", null, "What's on your mind?"),
        "Say it however it comes out. Tap the microphone on your keyboard to talk instead of type. " +
          (ai ? "Talk it through here, or save it straight away." : "When you save it, " + receiversOf(p.id) + " will see it.")) : null,
      state.chat.map((m) => h("div", { class: "bubble " + m.role }, m.content)),
      state.busy ? h("div", { class: "bubble assistant pending" }, "Thinking...") : null),
    h("div", { class: "composer" },
      chatInput(state.draft, "Type or dictate your idea", (v) => { state.draft = v; syncTalkButtons(); }, ai ? sendChat : () => saveDump(false)),
      h("div", { class: "row" },
        ai ? h("button", { class: "primary grow", id: "sendBtn", disabled: state.busy || !hasText, onclick: sendChat }, empty ? "Talk it through" : "Send") : null,
        empty
          ? h("button", { class: ai ? "quiet" : "primary grow", id: "saveBtn", disabled: state.busy || !hasText, onclick: () => saveDump(false) }, ai ? "Save as ideas" : "Save idea")
          : h("button", { class: "quiet", id: "saveBtn", disabled: state.busy, onclick: () => saveChat(null) }, "Save idea")),
      empty ? null : h("button", { class: "link", disabled: state.busy, onclick: discardChat }, "Start over")));
}

function syncTalkButtons() {
  const has = !!state.draft.trim();
  const send = document.getElementById("sendBtn"), save = document.getElementById("saveBtn");
  if (send) send.disabled = state.busy || !has;
  if (save && !state.chat.length) save.disabled = state.busy || !has;
}

function missedLine(i) {
  const t = i.card_title || "an idea";
  const who = i.actor_name;
  if (i.kind === "created") return who + " added: " + t;
  if (i.kind === "status") return i.detail === "Done / Live" ? t + " is live" : t + " moved to " + i.detail;
  if (i.kind === "answered") return who + " answered on: " + t;
  if (i.kind === "merged") return who + " merged an idea into: " + t;
  if (i.kind === "unmerged") return who + " made this its own idea again: " + t;
  if (i.kind === "code") return i.detail === "Removed" ? who + " removed what the code says from: " + t : "The code was checked for: " + t + (i.detail ? " (" + i.detail.toLowerCase() + ")" : "");
  if (i.kind === "scoped") return who + " scoped: " + t;
  if (i.kind === "build") return who + " started building: " + t;
  if (i.kind === "build_asked") return who + " asked from an AI app to build: " + t;
  if (i.kind === "build_declined") return who + " held off on building: " + t;
  if (i.kind === "stage") return t + " is now in " + i.detail;
  if (i.kind === "pr") return "Ready for review: " + t;
  if (i.kind === "conflicts") return i.detail === "fixing" ? "Fixing conflicts: " + t : i.detail === "fixed" ? "Conflicts fixed, ready to test again: " + t : "The agent couldn't fix the conflicts: " + t;
  if (i.kind === "approval_asked") return who + " sent for sign-off: " + t;
  if (i.kind === "approval_done") return "Signed off: " + t;
  if (i.kind === "approval_sent_back") return who + " sent back: " + t;
  return who + " commented on: " + t;
}

// One line for each idea: where it stands now. What led there (and anything else that happened) opens under it.
// A later step in the idea's life replaces an earlier one: "ready for review" doesn't show once the idea is live.
const MILESTONES = ["created", "build", "pr", "status", "stage"];
function missedGroups(items) {
  const order = [], by = {};
  for (const i of items) {
    const key = i.card_id || "x" + order.length;
    if (!by[key]) { by[key] = []; order.push(key); }
    by[key].push(i);
  }
  return order.map((key) => {
    const rows = by[key];   // newest first
    const steps = rows.filter((r) => MILESTONES.includes(r.kind));
    // The newest step is the headline. If two came at the same moment, a status beats the stage that went with it.
    const head = steps.length ? steps.reduce((a, b) => b.created_at > a.created_at || (b.created_at === a.created_at && b.kind === "status") ? b : a) : rows[0];
    return { key, head, rest: rows.filter((r) => r !== head && r.created_at !== head.created_at) };
  });
}

function missedBlock() {
  if (!state.missed.length) return null;
  const name = (i) => multiProject() && project(i.project_id) ? project(i.project_id).name + ": " : "";
  return h("section", { class: "missed" },
    h("h2", null, "While you were away"),
    h("ul", null, missedGroups(state.missed).map((g) => {
      const open = !!state.missedOpen[g.key];
      return h("li", null,
        h("div", { class: "row between" },
          h("button", { onclick: () => g.head.card_id && openCard(g.head.card_id) }, name(g.head) + missedLine(g.head)),
          g.rest.length ? h("button", { class: "link", "aria-expanded": String(open), "aria-label": (open ? "Hide" : "Show") + " what else happened",
            onclick: () => { state.missedOpen[g.key] = !open; render(); } }, open ? "Hide" : g.rest.length + " more") : null),
        open ? h("ul", { class: "missedmore" }, g.rest.map((r) => h("li", null, missedLine(r), h("small", null, " · " + fmtDate(r.created_at))))) : null);
    })),
    h("button", { class: "link", onclick: dismissMissed }, "Got it"));
}

function renderCards() {
  const kids = [];
  if (!multiProject()) kids.push(missedBlock());
  kids.push(pushBanner());
  if (state.cards.length || receiverAnywhere()) kids.push(viewToggle());
  if (state.view === "tools" && receiverAnywhere() && !state.asGiver) { kids.push(renderTools()); return h("main", null, kids); }
  if (state.view === "usage" && receiverAnywhere() && !state.asGiver) { kids.push(renderUsage()); return h("main", null, kids); }
  if (state.view === "shipped") { kids.push(renderShipped()); return h("main", null, kids); }
  kids.push(dupsBanner());
  if (!shownCards().length) kids.push(h("p", { class: "empty" }, multiProject() ? "No ideas in " + talkProject().name + " yet. Saved ideas will show here, grouped by where they stand." : "No ideas yet. Saved ideas will show here, grouped by where they stand."));
  else if (state.view === "board") { kids.push(renderBoard()); return h("main", { class: "boardmain" }, kids); }
  for (const key of GROUP_ORDER) {
    const cards = shownCards().filter((c) => bucket(c) === key);
    if (!cards.length) continue;
    kids.push(h("section", { class: "group" + (key === "needs" ? " attention" : "") },
      h("h2", null, groupTitle(key) + " (" + cards.length + ")"),
      cards.map(cardRow)));
  }
  return h("main", null, kids);
}

// ---------- ideas that say the same thing ----------

// Receivers only. Asked for when the list is opened and after a merge, not on every refresh.
async function loadDups() {
  const mine = state.me.projects.filter((p) => p.role === "receiver");
  if (!mine.length) return;
  try {
    const found = await Promise.all(mine.map((p) => api("/api/projects/" + encodeURIComponent(p.id) + "/duplicates")));
    const pairs = found.flatMap((r, i) => r.pairs.map((x) => ({ ...x, project_id: mine[i].id })));
    const changed = JSON.stringify(pairs) !== JSON.stringify(state.dups);
    state.dups = pairs;
    const typing = document.activeElement && ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement.tagName);
    if (changed && !typing && !state.busy && state.tab === "cards" && !state.cardId) render();
  } catch (_) {}
}

const titleButton = (x) => h("button", { class: "link title", onclick: () => openCard(x.id) }, x.title);

function dupsBanner() {
  const all = multiProject() ? state.dups.filter((p) => p.project_id === talkProject().id) : state.dups;
  if (state.asGiver || !all.length) return null;
  const shown = state.dupsOpen ? all : all.slice(0, 2);
  return h("section", { class: "missed dups" },
    h("h2", null, all.length === 1 ? "These two might be the same idea" : "Some of these might be the same idea"),
    shown.map((p) => h("div", { class: "pair" },
      h("div", { class: "pairnames" },
        h("div", null, titleButton(p.fold_in), h("small", null, p.fold_in.author_name + ", " + fmtDate(p.fold_in.created_at))),
        h("div", null, titleButton(p.keep), h("small", null, p.keep.author_name + ", " + fmtDate(p.keep.created_at) + (p.keep.being_built ? ", being built" : "")))),
      h("div", { class: "row" },
        h("button", { class: "quiet", disabled: state.busy, onclick: () => mergeCards(p.fold_in, p.keep) }, "Merge the first into the second"),
        h("button", { class: "quiet", disabled: state.busy, onclick: () => notSame(p.fold_in, p.keep) }, "Not the same")))),
    all.length > 2 ? h("button", { class: "link", onclick: () => { state.dupsOpen = !state.dupsOpen; render(); } },
      state.dupsOpen ? "Show fewer" : "Show all " + all.length) : null,
    h("p", { class: "hint" }, "Matched on their wording, so read both before merging. Merging can be undone from the merged idea."));
}

// ---------- board ----------

// The board's columns are the project's labels, in its order. Each counts as one core meaning (its kind), which decides what an
// idea there can do. Ideas move into the building kinds when a build starts, and nothing goes back to New.
const BUILDING = ["Active development", "Testing"];
const projectLabels = (pid) => project(pid)?.labels || [];
const columnsFor = (labels) => labels.map((l) => ({ key: l.name, title: l.name, kind: l.kind, status: l.status, to: l.name }));

function boardColumns() {
  const p = talkProject();
  return columnsFor(p?.labels || []);
}

// Where an idea sits: the label it was given if that column is there, else the label named like its status (how it was before
// teams named their own), else the first label that counts as the same thing.
function columnOf(c, cols) {
  const kind = (state.me.status_kinds || {})[c.status] || c.status;
  const label = String(c.label || "").toLowerCase();
  return (label && cols.find((x) => x.title.toLowerCase() === label && (x.status === c.status || x.kind === kind)))
    || (!label && cols.find((x) => x.kind === kind && x.title.toLowerCase() === c.status.toLowerCase()))
    || cols.find((x) => x.kind === kind) || cols[0];
}

// Whether this install's plan has a feature. The server refuses too; this only keeps what's off out of sight.
const can = (f) => !state.me || !state.me.plan || state.me.plan.features[f] !== false;

function viewToggle() {
  const b = (v, label) => h("button", { "aria-pressed": String(state.view === v), onclick: () => setView(v) }, label);
  return h("div", { class: "seg", role: "group", "aria-label": "View" }, b("list", "List"), b("board", "Board"), can("changelog") ? b("shipped", "Shipped") : null, receiverAnywhere() && !state.asGiver ? [b("tools", "Tools"), b("usage", "Usage")] : null);
}

function setView(v) {
  state.view = v;
  if (v === "tools") loadTools();
  if (v === "usage") loadUsage();
  if (v === "shipped") loadShipped();
  try { localStorage.setItem("innerface.view", v); } catch (_) {}
  render();
}

function renderBoard() {
  const cols = boardColumns();
  return h("div", { class: "board" }, cols.map((col) => {
    const cards = shownCards().filter((c) => columnOf(c, cols) === col);
    const el = h("section", { class: "col", "data-col": col.key },
      h("h2", null, col.title, h("span", { class: "count" }, String(cards.length))),
      h("div", { class: "colcards" }, cards.length ? cards.map(boardCard) : h("p", { class: "colempty" }, "Nothing here")));
    el.addEventListener("dragover", (e) => { if (state.dragId) { e.preventDefault(); el.classList.add("over"); } });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("over"); });
    el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("over"); dropOn(col); });
    return el;
  }));
}

// ---------- what shipped ----------

function loadShipped() {
  api("/api/shipped" + (multiProject() ? "?project_id=" + encodeURIComponent(talkProject().id) : "")).then((r) => { state.shipped = r; render(); }).catch((e) => toast(e.message));
}

const weekTitle = (day) => "Week of " + new Date(day + "T12:00:00").toLocaleDateString(undefined, { month: "long", day: "numeric" });
const tryLink = (url, label) => /^https:\/\//.test(url || "") ? h("a", { class: "try", href: url, target: "_blank", rel: "noopener noreferrer" }, label || "Try it") : null;

// What went live, week by week, in the words on the idea. Everyone in the project sees it.
function renderShipped() {
  const d = state.shipped;
  if (!d) return h("p", { class: "empty" }, "Loading...");
  if (!d.count) return h("p", { class: "empty" }, "Nothing has shipped in the last " + d.looked_back_weeks + " weeks. Ideas show here once they are Done / Live.");
  return h("div", { class: "shipped" },
    h("div", { class: "row between" },
      h("p", { class: "hint" }, plural(d.count, "idea") + " went live in the last " + d.looked_back_weeks + " weeks."),
      h("button", { class: "quiet", onclick: () => navigator.clipboard.writeText(d.text).then(() => toast("Copied. Paste it into a message."), () => toast("Couldn't copy")) }, "Copy as text")),
    d.weeks.map((w) => h("section", { class: "group" },
      h("h2", null, weekTitle(w.week_of) + " (" + w.items.length + ")"),
      w.items.map((i) => h("div", { class: "card shippedrow" },
        h("button", { class: "link title", onclick: () => openCard(i.id) }, i.title),
        i.summary ? h("p", null, i.summary) : null,
        h("div", { class: "meta" },
          i.yours ? h("span", { class: "chip DoneLive" }, "Your idea") : h("span", null, "From " + i.from),
          h("span", null, fmtDate(i.shipped_at)),
          tryLink(i.try_url)))))));
}

// ---------- tools ----------

const pct = (x) => x === null || x === undefined ? "–" : Math.round(x * 100) + "%";

function loadTools() {
  const mine = state.me.projects.filter((p) => p.role === "receiver");
  Promise.all(mine.map((p) => api("/api/projects/" + encodeURIComponent(p.id) + "/tools").then((r) => [p.id, r.tools])))
    .then((pairs) => { state.tools = Object.fromEntries(pairs); render(); })
    .catch((e) => toast(e.message));
}

function renderTools() {
  if (!state.tools) return h("p", { class: "empty" }, "Loading...");
  const mine = state.me.projects.filter((p) => p.role === "receiver" && (!multiProject() || p.id === talkProject().id));
  return h("div", null, mine.map((p) => {
    const rows = state.tools[p.id] || [];
    return h("section", { class: "group" },
      h("h2", null, (multiProject() ? p.name + ": " : "") + "How each tool is doing, last 30 days"),
      rows.length ? h("div", { class: "tooltable", role: "table" },
        h("div", { class: "tr th", role: "row" }, ["Tool", "Liked", "Before", "Uses", "Failed", "Redo", "Ideas"].map((x) => h("span", { role: "columnheader" }, x))),
        rows.map((r) => {
          const worse = (now, before, by) => now !== null && before !== null && now - before >= by;
          const liked = worse(r.previous_positive_share, r.positive_share, 0.1);
          const failed = worse(r.failure_rate, r.previous_failure_rate, 0.03);
          const redone = worse(r.rerun_rate, r.previous_rerun_rate, 0.1);
          const cell = (text, bad) => h("span", { role: "cell", class: bad ? "bad" : null }, text + (bad ? " ↑" : ""));
          return h("div", { class: "tr", role: "row" },
            h("span", { role: "cell" }, r.label),
            h("span", { role: "cell", class: liked ? "bad" : null }, pct(r.positive_share) + (liked ? " ↓" : "")),
            h("span", { role: "cell" }, pct(r.previous_positive_share)),
            h("span", { role: "cell" }, r.uses === null ? "–" : String(r.uses)),
            cell(pct(r.failure_rate), failed),
            cell(pct(r.rerun_rate), redone),
            h("span", { role: "cell" }, String(r.ideas)));
        }),
        h("p", { class: "hint tablenote" }, "Liked: share of positive ratings. Failed: runs that errored. Redo: run again within 10 minutes. Arrows mark a clear change from the 30 days before."))
        : h("p", { class: "empty" }, "Nothing yet. Tool ratings arrive from connected sources (Settings > Sources)."));
  }));
}

// ---------- usage ----------

const PERIODS = [[30, "30 days"], [90, "90 days"], [365, "Year"]];
const plural = (n, one, many) => n + " " + (n === 1 ? one : many || one + "s");

// A length of time in the largest unit that reads naturally.
function span(hours) {
  if (hours === null || hours === undefined) return "–";
  if (hours < 1) return plural(Math.max(1, Math.round(hours * 60)), "min", "min");
  if (hours < 48) return plural(hours < 10 ? Math.round(hours * 10) / 10 : Math.round(hours), "hour");
  const days = hours / 24;
  return plural(days < 10 ? Math.round(days * 10) / 10 : Math.round(days), "day");
}

function loadUsage(days) {
  if (days) { state.usageDays = days; state.usage = null; render(); }
  const mine = state.me.projects.filter((p) => p.role === "receiver");
  const asked = state.usageDays;
  Promise.all(mine.map((p) => api("/api/projects/" + encodeURIComponent(p.id) + "/usage?days=" + asked).then((r) => [p.id, r])))
    .then((pairs) => { if (asked === state.usageDays) { state.usage = Object.fromEntries(pairs); render(); } })
    .catch((e) => toast(e.message));
}

// Horizontal bars in one color: the length is the count, and the number sits at the end of the bar.
function bars(rows, note) {
  const top = Math.max(1, ...rows.map((r) => r.count));
  return h("div", { class: "bars", role: "list" }, rows.map((r) => {
    const text = String(r.count) + (note ? note(r) : "");
    const fill = h("span", { class: "barfill" });
    fill.style.setProperty("--w", (r.count / top).toFixed(3));   // set from script: the page's policy blocks style attributes
    return h("div", { class: "bar", role: "listitem", title: r.label + ": " + text },
      h("span", { class: "barlabel" }, r.label),
      h("span", { class: "bartrack" }, fill, h("span", { class: "barvalue" + (r.count ? "" : " none") }, text)));
  }));
}

function tile(label, value, sub) {
  return h("div", { class: "tile" }, h("span", { class: "tlabel" }, label), h("span", { class: "tvalue" }, value), h("span", { class: "tsub" }, sub));
}

function renderUsage() {
  const days = state.usageDays;
  const period = (PERIODS.find((p) => p[0] === days) || [days, days + " days"])[1].toLowerCase();
  const pick = h("div", { class: "seg", role: "group", "aria-label": "Period" },
    PERIODS.map(([d, label]) => h("button", { "aria-pressed": String(days === d), onclick: () => loadUsage(d) }, label)));
  if (!state.usage) return h("div", null, pick, h("p", { class: "empty" }, "Loading..."));
  const mine = state.me.projects.filter((p) => p.role === "receiver" && (!multiProject() || p.id === talkProject().id));
  return h("div", { class: "usage" }, pick, mine.map((p) => {
    const u = state.usage[p.id];
    if (!u) return null;
    const name = multiProject() ? h("h2", { class: "usename" }, p.name) : null;
    if (!u.ideas.total) return h("section", { class: "group" }, name, h("p", { class: "empty" }, "No ideas came in over the last " + period + "."));
    const t = u.times, total = u.ideas.total;
    const timed = (x, what) => x.count ? "Middle of " + plural(x.count, "idea") : what;
    const share = (r) => r.key === "received" ? "" : " · " + Math.round(r.count / total * 100) + "%";
    const chans = u.actions.channels;
    const sum = Object.fromEntries(chans.map((c) => [c.key, u.actions.rows.reduce((n, r) => n + r.counts[c.key], 0)]));
    const row = (label, counts, cls) => h("div", { class: "tr" + (cls ? " " + cls : ""), role: "row" },
      h("span", { role: cls === "th" ? "columnheader" : "cell" }, label),
      chans.map((c) => h("span", { role: cls === "th" ? "columnheader" : "cell", class: "num" + (counts[c.key] === 0 ? " none" : "") }, String(counts[c.key]))));
    const people = u.people;
    return h("section", { class: "group usegroup" },
      name,
      h("div", { class: "tiles" },
        tile("Ideas received", String(total), u.ideas.previous + " in the " + period + " before"),
        tile("First response", span(t.first_response.hours), timed(t.first_response, "No one has responded yet")),
        tile("Idea to build", span(t.to_build.hours), timed(t.to_build, "No builds started yet")),
        tile("Build to done", span(t.build_to_done.hours), timed(t.build_to_done, "No builds finished yet"))),
      h("div", { class: "panel" },
        h("h3", null, "Where ideas came from"),
        bars(u.ideas.by_source.slice().sort((a, b) => b.count - a.count))),
      h("div", { class: "panel" },
        h("h3", null, "How far they got"),
        bars(u.progress, share),
        u.rejected ? h("p", { class: "hint" }, plural(u.rejected, "idea was", "ideas were") + " turned down.") : null),
      h("div", { class: "panel" },
        h("h3", null, "Where the work was done"),
        h("div", { class: "tooltable usetable", role: "table" },
          row("", Object.fromEntries(chans.map((c) => [c.key, c.label])), "th"),
          u.actions.rows.map((r) => row(r.label, r.counts)),
          row("All", sum, "sum")),
        u.actions.unrecorded ? h("p", { class: "hint" }, plural(u.actions.unrecorded, "earlier action was", "earlier actions were") + " made before this was recorded, so " + (u.actions.unrecorded === 1 ? "it isn't" : "they aren't") + " in the table.") : null),
      h("p", { class: "hint" }, plural(people.givers, "person", "people") + " sent ideas or replies and " + plural(people.receivers, "person", "people") + " reviewed them."));
  }), h("p", { class: "hint usenote" }, "Times are the middle value, so one slow idea doesn't skew them. These numbers are worked out on this server and never leave it."));
}

function boardCard(c) {
  const el = cardRow(c);
  if (receives(c.project_id)) {
    el.setAttribute("draggable", "true");
    el.addEventListener("dragstart", (e) => { state.dragId = c.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.id); el.classList.add("dragging"); });
    el.addEventListener("dragend", () => { state.dragId = null; el.classList.remove("dragging"); });
  }
  return el;
}

function dropOn(col) {
  const c = state.cards.find((x) => x.id === state.dragId);
  state.dragId = null;
  if (!c || columnOf(c, boardColumns()) === col) return;
  if (col.kind === "New") { toast("Ideas can't be moved back to New"); return; }
  if (BUILDING.includes(col.kind) && !c.build_started_at) { toast("Open the idea and use Start build"); return; }
  if (col.kind === "Needs info") { openCard(c.id).then(() => { toast("Type your question for " + c.author_name); const m = document.getElementById("ownerMsg"); if (m) m.focus(); }); return; }
  run(async () => {
    const r = await api("/api/cards/" + c.id, { method: "PATCH", body: { status: col.to } });
    toast(r.waiting_for_approval ? r.tell_the_user : "Moved to " + col.to);
    await refresh();
  });
}

function cardRow(c) {
  const needs = c.status === "Needs info";
  return h("button", { class: "card", onclick: () => openCard(c.id) },
    h("h3", null, c.title),
    needs && c.note ? h("p", { class: "ask" }, c.note) : c.summary ? h("p", null, c.summary) : null,
    h("div", { class: "meta" },
      h("span", { class: "chip " + statusClass(c.status) }, c.label || c.status),
      c.conflict && c.status !== "Done / Live" ? h("span", { class: "chip okwait" }, c.conflict === "fixing" ? "Fixing conflicts" : "Conflicts need a person") : null,
      c.build_ask && receives(c.project_id) ? h("span", { class: "chip okwait" }, "Waiting for your OK") : null,
      c.approval ? h("span", { class: "chip okwait" }, "Awaiting sign-off, level " + c.approval.level + " of " + c.approval.of) : null,
      multiProject() ? h("span", { class: "chip" }, c.project_name) : null,
      c.external ? h("span", { class: "chip source" }, c.source_name) : null,
      c.tool ? h("span", { class: "chip" }, c.tool) : null,
      c.priority ? h("span", { class: "chip" }, c.priority) : null,
      c.tags.map((t) => h("span", { class: "chip" }, t)),
      h("span", null, (c.external ? "" : c.author_name + ", ") + fmtDate(c.created_at))));
}

function field(label, text) { return text ? h("div", { class: "field" }, h("h4", null, label), h("p", null, text)) : null; }
function listField(label, items) { return items && items.length ? h("div", { class: "field" }, h("h4", null, label), h("ul", null, items.map((q) => h("li", null, q)))) : null; }
function extLink(href, text) { return /^https:\/\/github\.com\//.test(href || "") ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text) : null; }

function outLink(l) { return /^https:\/\//.test(l.url || "") ? h("a", { href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.label) : h("span", { class: "chip" }, l.label); }

function codeLink(k, path) {
  const ok = /^[\w.-]+\/[\w.-]+$/.test(k.repo || "") && /^[0-9a-f]{7,40}$/.test(k.commit || "");
  if (!ok) return h("code", null, path);
  const href = "https://github.com/" + k.repo + "/blob/" + k.commit + "/" + path.split("/").map(encodeURIComponent).join("/");
  return h("a", { href, target: "_blank", rel: "noopener noreferrer" }, h("code", null, path));
}

// What an AI app found when it looked through the project's code for this idea.
function codeCheck(c, mine) {
  const k = c.code_check;
  if (!k) return null;
  const when = "Looked up for " + k.by_name + " by an AI app on " + fmtDate(k.at) + (k.commit ? ", with the code as it was at " + k.commit : "") + ".";
  return h("div", { class: "panel codecheck" },
    h("h3", null, "What the code says"),
    h("div", { class: "meta" }, h("span", { class: "chip says-" + k.says }, k.label)),
    h("p", null, k.summary),
    k.places.length ? h("ul", { class: "places" }, k.places.map((x) => h("li", null, codeLink(k, x.path), x.why ? h("span", null, x.why) : null))) : null,
    k.places_hidden ? h("p", { class: "hint" }, "The files involved are listed for people who can look at the code.") : null,
    h("p", { class: "hint" }, when + (k.verified ? "" : " The file names couldn't be checked against the repo.") + " Treat it as a head start; the code may have moved on."),
    mine && !c.build_started_at ? h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: () => removeCodeCheck(c.id) }, "Remove")) : null);
}

function similarPanel(c, mine) {
  if (c.merged_into || !(c.similar || []).length) return null;
  return h("div", { class: "panel similar" },
    h("h3", null, "Might be the same as"),
    c.similar.map((x) => h("div", { class: "pair" },
      h("div", { class: "pairnames" }, h("div", null, titleButton(x),
        h("small", null, h("span", { class: "chip " + statusClass(x.status) }, x.status), " " + x.author_name + ", " + fmtDate(x.created_at)))),
      mine ? h("div", { class: "row" },
        c.build_started_at ? null : h("button", { class: "quiet", disabled: state.busy, onclick: () => mergeCards(c, x) }, "Merge this into it"),
        x.being_built ? null : h("button", { class: "quiet", disabled: state.busy, onclick: () => mergeCards(x, c) }, "Merge it into this"),
        h("button", { class: "quiet", disabled: state.busy, onclick: () => notSame(c, x) }, "Not the same")) : null)),
    mine ? null : h("p", { class: "hint" }, "If it's the same idea, " + receiversOf(c.project_id) + " can merge the two."));
}

function mergedInPanel(c, mine) {
  if (!(c.merged_in || []).length) return null;
  return h("div", { class: "panel" },
    h("h3", null, c.merged_in.length === 1 ? "Includes another idea" : "Includes " + c.merged_in.length + " other ideas"),
    c.merged_in.map((x) => h("div", { class: "pair" },
      h("div", { class: "pairnames" }, h("div", null, titleButton(x), h("small", null, x.author_name + ", " + fmtDate(x.created_at)))),
      mine && x.direct ? h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: () => unmergeCard(x) }, "Split back out")) : null)));
}

function mergedBanner(c, mine) {
  if (!c.merged_into) return null;
  return h("div", { class: "banner" },
    c.became ? h("span", null, "This idea is now part of ", titleButton(c.became), ". Comments and updates happen there.")
      : h("span", null, "This idea was merged into another one."),
    mine ? h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: () => unmergeCard(c) }, "Split back out")) : null);
}

// What the write-up still lacks before a build. It informs: Start build works either way.
function readyList(c) {
  const r = c.ready;
  if (!r) return null;
  return h("div", { class: "ready", id: "ready" },
    h("h4", { class: r.ready ? "ok" : "open" }, r.headline),
    r.gaps.length ? h("ul", { class: "gaps" }, r.gaps.map((g) => h("li", { class: g.weight }, h("span", null, g.what), h("small", null, g.next)))) : null,
    r.ready ? null : h("p", { class: "hint" }, "You can still start the build. Whoever builds it will have to guess at these."));
}

function stillOpen(c) {
  return c.ready && !c.ready.ready ? c.ready.gaps.filter((g) => g.weight === "missing").map((g) => g.what) : [];
}

// A build asked for from an AI app. It waits here until a receiver starts it or says not now.
function askBanner(c, mine) {
  if (!mine || !c.build_ask || c.build_started_at || c.merged_into) return null;
  const who = c.build_ask.by === state.me.email ? "You" : c.build_ask.by_name;
  const where = listNames(project(c.project_id)?.build_targets || []);
  return h("div", { class: "callout", id: "buildAsk" },
    h("h4", null, "Waiting for your OK to build"),
    h("p", null, who + " asked for this build from an AI app on " + fmtDate(c.build_ask.at) + ". Nothing has been sent yet. Read the scope below first: it goes to " + (where || "your build tools") + " as written, and it is locked once the build starts."),
    stillOpen(c).length ? h("p", { id: "askOpen" }, c.ready.headline + ": " + stillOpen(c).join(" ")) : null,
    h("div", { class: "row" },
      h("button", { class: "primary", disabled: state.busy, onclick: () => startBuild(c, null, true) }, "Start build"),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => declineBuild(c) }, "Not now")));
}

// Once an idea is live, say so to whoever had it, with a way to go and try it.
function liveBanner(c, mine) {
  if (c.status !== "Done / Live" || c.merged_into) return null;
  const key = "try:" + c.id;
  const usual = project(c.project_id)?.live_url || "";
  const url = c.try_url || usual;
  return h("div", { class: "callout live", id: "liveNote" },
    h("h4", null, c.mine ? "Your idea is live" : "This is live"),
    h("p", null, "It shipped on " + fmtDate(c.shipped_at || c.updated_at) + "." + (url ? "" : mine ? " Add a link below so people can try it." : "")),
    url ? h("div", { class: "row" }, tryLink(url, "Try it")) : null,
    mine ? h("div", { class: "row" },
      h("input", { type: "url", inputmode: "url", autocapitalize: "off", spellcheck: "false", "aria-label": "Link to try this idea", placeholder: usual ? "A link for just this idea (now: " + usual + ")" : "https://… where people can try it",
        value: key in state.drafts ? state.drafts[key] : c.try_url || "", oninput: (e) => { state.drafts[key] = e.target.value; } }),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => saveTryLink(c, key) }, "Save link")) : null);
}

function saveTryLink(c, key) {
  if (state.drafts[key] === undefined) { toast("Nothing changed"); return; }
  run(async () => {
    await api("/api/cards/" + c.id + "/try-link", { method: "PUT", body: { url: state.drafts[key].trim() } });
    delete state.drafts[key];
    await loadCard(c.id);
    toast("Saved");
  });
}

function renderDetail() {
  const c = state.card;
  if (!c) return h("main", null, h("p", { class: "empty" }, "Loading..."));
  const needs = c.status === "Needs info";
  const mine = receives(c.project_id);
  const previewing = state.asGiver && project(c.project_id)?.role === "receiver";
  const asker = receiversOf(c.project_id);
  return h("main", null, h("div", { class: "detail" },
    h("div", null,
      h("h2", null, c.title),
      h("div", { class: "meta" },
        h("span", { class: "chip " + statusClass(c.status) }, c.label || c.status),
        c.conflict && c.status !== "Done / Live" ? h("span", { class: "chip okwait" }, c.conflict === "fixing" ? "Fixing conflicts" : "Conflicts need a person") : null,
        multiProject() ? h("span", { class: "chip" }, c.project_name) : null,
        c.tool ? h("span", { class: "chip" }, c.tool) : null,
        c.priority ? h("span", { class: "chip" }, c.priority) : null,
        c.tags.map((t) => h("span", { class: "chip" }, t)),
        h("span", null, c.author_name + ", " + fmtDate(c.created_at) + (c.source === "mcp" ? ", sent from an AI app" : "")))),
    c.external ? h("div", { class: "banner" },
      h("span", null, "Came in from " + c.source_name + ". Whoever wrote it can't see or answer anything here."),
      c.flagged ? h("strong", null, " Personal details were removed automatically; some may remain.") : null,
      /^https:\/\//.test(c.source_url || "") ? h("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer" }, " Open the original") : null) : null,
    mergedBanner(c, mine),
    askBanner(c, mine),
    typeof approvalBanner === "function" ? approvalBanner(c) : null,
    liveBanner(c, mine),
    c.issue_url || (c.links || []).length || c.thread ? h("div", { class: "row links" },
      extLink(c.issue_url, "Issue #" + c.issue_number), extLink(c.pr_url, "Pull request"),
      (c.links || []).filter((l) => l.kind !== "github").map(outLink), c.thread ? outLink(c.thread) : null) : null,
    c.conflict === "fixing" && c.status !== "Done / Live" ? h("p", { class: "hint" }, "Fixing conflicts. The pull request can't merge into the main branch any more, so the build runner has sent the agent back to rebase it. You'll get an alert when it's ready to test again.") : null,
    c.conflict === "stuck" && c.status !== "Done / Live" ? h("div", { class: "callout" }, h("h4", null, "Conflicts need a person"), h("p", null, "The agent couldn't resolve the conflicts on its own. Open the pull request and fix them there.")) : null,
    c.build_stage && c.status !== "Done / Live" ? h("p", { class: "hint" }, "Now in " + c.build_stage + ". That is where its code has got to.") : null,
    needs && c.note ? h("div", { class: "callout" },
      h("h4", null, mine ? "You asked" : asker + " asks"),
      h("p", null, c.note),
      !mine && !previewing ? replyBox(c) : null) : null,
    !needs && c.note ? field(mine ? "Your note" : "From " + asker, c.note) : null,
    previewing ? null : similarPanel(c, mine),
    field("Summary", c.summary), field("Who it's for", c.who), field("What it solves", c.problem), field("Scope", c.scope),
    listField("Done when", c.acceptance), listField("Still undecided", c.open_questions),
    codeCheck(c, mine),
    field("Original words", c.source === "chat" ? "" : c.original_text),
    c.transcript.length ? h("details", null, h("summary", null, "Full conversation"),
      h("div", { class: "transcript" }, c.transcript.map((m) => h("div", { class: "bubble " + m.role }, m.content)))) : null,
    c.comments.length ? h("div", { class: "panel" }, h("h3", null, "Discussion"),
      h("div", null, c.comments.map((m) => h("div", { class: "comment" }, h("small", null, m.author_name + ", " + fmtDate(m.created_at)), m.text)))) : null,
    mergedInPanel(c, mine),
    historyPanel(c),
    previewing ? h("p", { class: "banner" }, "Preview only. Givers can comment, react and answer questions here.")
      : c.merged_into ? null : mine ? receiverPanel(c) : giverPanel(c)));
}

// Who did what to an idea, in order. Everyone who can see the idea can see this.
const HISTORY = {
  created: () => "added the idea", status: (w) => "moved it to " + w, scoped: () => "updated the scope", build: (w) => "started the build (" + w + ")",
  build_declined: () => "held off on the build", merged: () => "merged an idea into it", unmerged: () => "made it its own idea again",
  code: (w) => "checked the code" + (w ? " (" + w.toLowerCase() + ")" : ""), stage: (w) => "saw its code reach " + w, pr: () => "opened a pull request",
  conflicts: (w) => w === "fixing" ? "saw the build being sent back to fix conflicts" : w === "fixed" ? "saw the conflicts fixed" : "saw the agent give up on the conflicts",
  comment: () => "commented", answered: () => "answered", build_asked: () => "asked for the build",
  approval_asked: (w) => "sent it for sign-off " + w, approval_signed: (w) => "signed it off, " + w.toLowerCase(), approval_self: (w) => w,
  approval_covered: (w) => "covered a level, " + w.toLowerCase(), approval_done: (w) => w, approval_sent_back: (w) => "sent it back: " + w,
  approval_withdrawn: (w) => "took it out of sign-off: " + w,
};
const VIA = { mcp: "from an AI app", slack: "from Slack", api: "from an outside system", github: "" };

function historyPanel(c) {
  const rows = c.history || [];
  if (!rows.length) return null;
  return h("details", { class: "panel", id: "history" },
    h("summary", null, "History (" + rows.length + ")"),
    h("ul", { class: "sources" }, rows.map((x) => h("li", null,
      h("span", null, h("strong", null, x.who), " " + (HISTORY[x.kind] ? HISTORY[x.kind](x.what || "") : x.kind) + (VIA[x.via] ? " " + VIA[x.via] : ""),
        h("span", { class: "hint" }, ", " + fmtDate(x.at)))))));
}

function replyBox(c) {
  const key = "reply:" + c.id;
  return h("div", { class: "row" },
    h("textarea", { rows: "3", placeholder: "Type or dictate your answer", "aria-label": "Your answer", value: state.drafts[key] || "", oninput: (e) => { state.drafts[key] = e.target.value; } }),
    h("button", { class: "primary", disabled: state.busy, onclick: () => postComment(c.id, "comment", key) }, "Send answer"));
}

function discussBlock(c, placeholder, saveLabel) {
  const key = "chat:" + c.id;
  return [
    state.cardChat.length || state.busy ? h("div", { class: "transcript" },
      h("div", { class: "chat-inline" }, state.cardChat.map((m) => h("div", { class: "bubble " + m.role }, m.content)),
        state.busy ? h("div", { class: "bubble assistant pending" }, "Thinking...") : null)) : null,
    h("textarea", { rows: "2", placeholder, "aria-label": placeholder, value: state.drafts[key] || "", oninput: (e) => { state.drafts[key] = e.target.value; } }),
    h("div", { class: "row" },
      h("button", { class: "quiet", disabled: state.busy, onclick: () => sendCardChat(c.id, key) }, "Ask"),
      saveLabel && state.cardChat.length ? h("button", { class: "primary", disabled: state.busy, onclick: () => saveChat(c.id) }, saveLabel) : null),
  ];
}

function giverPanel(c) {
  const key = "comment:" + c.id;
  return [h("div", { class: "panel" },
    h("h3", null, "Add to this idea"),
    c.status === "Needs info" ? null : h("textarea", { rows: "2", placeholder: "Add a comment", "aria-label": "Add a comment", value: state.drafts[key] || "", oninput: (e) => { state.drafts[key] = e.target.value; } }),
    h("div", { class: "row" },
      c.status === "Needs info" ? null : h("button", { class: "quiet", disabled: state.busy, onclick: () => postComment(c.id, "comment", key) }, "Comment"),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => postComment(c.id, "dislike") }, "I don't like this"),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => postComment(c.id, "review") }, "Let's review together"))),
    state.me.ai ? h("div", { class: "panel" }, h("h3", null, "Talk it through"), discussBlock(c, "Keep working on this idea", "Save to this idea")) : null];
}

function scopeEditor(c) {
  const k = (f) => "scope:" + c.id + ":" + f;
  const val = (f, fallback) => k(f) in state.drafts ? state.drafts[k(f)] : fallback;
  const input = (f, label, fallback, rows) => h("label", { class: "stack" }, h("span", null, label),
    h(rows ? "textarea" : "input", { rows: rows ? String(rows) : null, "aria-label": label, value: val(f, fallback), oninput: (e) => { state.drafts[k(f)] = e.target.value; } }));
  const locked = !!c.build_started_at;
  return h("details", null,
    h("summary", null, locked ? "Scope (locked while it's being built)" : "Edit scope"),
    locked ? null : h("div", { class: "stack-form" },
      input("title", "Title", c.title),
      input("summary", "Summary", c.summary, 3),
      input("who", "Who it's for", c.who, 2),
      input("problem", "What it solves", c.problem, 2),
      input("scope", "Scope: what's in and out", c.scope, 4),
      input("acceptance", "Done when (one per line)", c.acceptance.join("\n"), 4),
      input("open_questions", "Still undecided (one per line)", c.open_questions.join("\n"), 3),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => saveScope(c, k) }, "Save scope")));
}

function receiverPanel(c) {
  const key = "msg:" + c.id;
  const tagKey = "tags:" + c.id;
  const author = c.author_name;
  const mergeTargets = c.build_started_at ? [] : state.cards.filter((x) => x.id !== c.id && x.project_id === c.project_id);
  const canBuild = project(c.project_id)?.builds && !c.build_started_at;
  const unsent = c.unsent || [];
  return [h("div", { class: "panel" },
    h("h3", null, c.external ? "File" : "Send back or file"),
    c.external ? null : h("textarea", { id: "ownerMsg", rows: "2", placeholder: "Question or note for " + author, "aria-label": "Question or note for " + author, value: state.drafts[key] || "", oninput: (e) => { state.drafts[key] = e.target.value; } }),
    c.external ? null : h("div", { class: "row" },
      h("button", { class: "primary", disabled: state.busy, onclick: () => patch(c.id, { status: "Needs info" }, key) }, "Send back to " + author),
      state.me.ai ? h("button", { class: "quiet", disabled: state.busy, onclick: () => draftQuestion(c.id, key) }, "Draft a question") : null),
    h("div", { class: "row" }, fileLabels(c).map((l) => h("button", { class: "quiet", "aria-pressed": String(columnOf(c, columnsFor(projectLabels(c.project_id)))?.title === l.name), disabled: state.busy, onclick: () => patch(c.id, { status: l.name }, key) }, l.name)))),
  h("div", { class: "panel" },
    h("h3", null, "Scope and build"),
    scopeEditor(c),
    canBuild ? readyList(c) : null,
    canBuild ? h("div", { class: "row" }, h("button", { class: "primary", disabled: state.busy, onclick: () => startBuild(c) }, "Start build")) : null,
    unsent.length ? h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: () => startBuild(c, unsent) }, "Send to " + listNames(unsent) + " too")) : null),
  h("div", { class: "panel" },
    h("h3", null, "Priority and tags"),
    h("div", { class: "row" }, state.me.priorities.filter(Boolean).map((p) => h("button", { class: "quiet", "aria-pressed": String(c.priority === p), disabled: state.busy, onclick: () => patch(c.id, { priority: c.priority === p ? "" : p }) }, p))),
    h("div", { class: "row" },
      h("input", { placeholder: "Tags, separated by commas", "aria-label": "Tags", value: tagKey in state.drafts ? state.drafts[tagKey] : c.tags.join(", "), oninput: (e) => { state.drafts[tagKey] = e.target.value; } }),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => { const v = state.drafts[tagKey]; if (v !== undefined) patch(c.id, { tags: v.split(",") }); } }, "Save tags"))),
  state.me.ai ? h("div", { class: "panel" }, h("h3", null, "Talk it through (only you see this)"), discussBlock(c, "Ask about this idea", null)) : null,
  h("div", { class: "panel" },
    h("div", { class: "row" }, h("button", { class: "quiet", onclick: () => copySpec(c.id) }, "Copy as spec")),
    mergeTargets.length ? h("details", null, h("summary", null, "Merge into another idea"),
      h("div", { class: "row" },
        h("select", { id: "mergeInto", "aria-label": "Merge into" }, mergeTargets.map((x) => h("option", { value: x.id }, x.title))),
        h("button", { class: "quiet", disabled: state.busy, onclick: () => merge(c) }, "Merge into that idea"))) : null)];
}

// Settings is a lot, so it is split by what you are doing. The project picker only shows where a tab belongs to one project.
const SETTINGS_TABS = [
  ["you", "You", false], ["people", "People", true], ["connectors", "Connectors", true], ["board", "Board", true],
  ["share", "Sharing", true], ["new", "New project", false],
];

// A section that opens and closes, with a line on the heading saying where it stands, so a long tab can be read at a glance.
function fold(key, title, note, kids) {
  return h("details", { class: "fold", open: !!state.open[key], ontoggle: (e) => { state.open[key] = e.target.open; } },
    h("summary", null, h("strong", null, title), note ? h("span", { class: "foldnote" }, note) : null), kids);
}

function connectorsTab(p) {
  const g = state.github[p.id], c = state.connectors[p.id], k = state.sources[p.id], r = state.runner[p.id];
  const live = c ? c.connectors.filter((x) => x.enabled) : [];
  return [
    h("p", { class: "hint" }, "Where ideas come in from, and where a build goes to."),
    fold("gh:" + p.id, "GitHub and build flow", g ? (g.repo ? g.repo + (g.token_set ? ", token saved" : ", no token yet") : "not linked yet") : "", [githubForm(p), flowForm(p)]),
    fold("runner:" + p.id, "Build runner", r ? (r.key_set ? (r.enabled ? "on, checks every " + plural(r.interval_minutes, "minute") : "switched off here") + (r.last_seen_at ? ", last checked in " + fmtDate(r.last_seen_at) : "") : "set up on your machine") : "", [runnerForm(p)]),
    fold("conn:" + p.id, "Chat and tracker connectors", c ? (live.length ? live.map((x) => x.name).join(", ") : "none yet") : "", [connectorsForm(p)]),
    fold("keys:" + p.id, "Intake keys", k ? (k.filter((x) => !x.revoked).length ? plural(k.filter((x) => !x.revoked).length, "key") : "none yet") : "", [sourcesForm(p)]),
  ];
}

function renderSettings() {
  const mine = state.me.projects.filter((p) => p.role === "receiver");
  if (!SETTINGS_TABS.some((t) => t[0] === state.settingsTab)) state.settingsTab = "you";
  const p = mine.find((x) => x.id === state.settingsProject) || mine[0];
  const tab = SETTINGS_TABS.find((t) => t[0] === state.settingsTab);
  const body = {
    you: () => [voiceForm()],
    people: () => [peopleForm(p)],
    board: () => [labelsForm(p), typeof approvalsForm === "function" ? approvalsForm(p) : approvalsNote(p)],
    connectors: () => connectorsTab(p),
    share: () => [shareForm(p)],
    new: () => [newProjectForm()],
  }[tab[0]]();
  return h("main", null,
    h("h2", null, "Settings"),
    h("div", { class: "seg", role: "group", "aria-label": "Settings sections" },
      SETTINGS_TABS.map(([id, label]) => h("button", { "aria-pressed": String(id === state.settingsTab), onclick: () => setSettingsTab(id) }, label))),
    tab[2] && mine.length > 1 ? h("label", { class: "stack" }, h("span", null, "Project"),
      h("select", { "aria-label": "Project", onchange: (e) => { state.settingsProject = e.target.value; render(); } },
        mine.map((x) => h("option", { value: x.id, selected: x.id === p.id }, x.name)))) : null,
    body);
}

function setSettingsTab(id) {
  state.settingsTab = id;
  try { localStorage.setItem("innerface.settingsTab", id); } catch (_) {}
  render();
}

// Where this person is signed in, and which AI apps act for them. Everyone has this, givers included.
function renderAccount() {
  const a = state.account;
  const used = (x) => "Since " + fmtDate(x.since) + ". Last used " + fmtDate(x.last_used) + ".";
  return h("main", null,
    h("h2", null, "Account"),
    h("section", { class: "panel", id: "account" },
      h("h3", null, "Signed in as " + state.me.name),
      h("p", { class: "hint" }, state.me.email),
      h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: signOut }, "Sign out"))),
    h("section", { class: "panel", "data-browsers": "" },
      h("h3", null, "Where you're signed in"),
      !a ? h("p", null, "Loading...") : h("ul", { class: "sources people" }, a.browsers.map((x) => h("li", null,
        h("span", { class: "who" }, h("strong", null, x.device + (x.this_one ? " (this one)" : "")), h("span", { class: "hint" }, used(x))),
        x.this_one ? null : h("button", { class: "link", disabled: state.busy, onclick: () => endSignIn("browsers", x.id, "Signed out there") }, "Sign out"))))),
    h("section", { class: "panel", "data-apps": "" },
      h("h3", null, "AI apps"),
      h("p", null, "To let Claude, ChatGPT or another AI app send in ideas and work on them as you, add a custom connector in that app with this address. It sends you back here to agree."),
      a ? h("div", { class: "row" },
        h("code", { class: "key" }, a.connector_url),
        h("button", { class: "quiet", onclick: () => navigator.clipboard.writeText(a.connector_url).then(() => toast("Copied"), () => toast("Couldn't copy. Select it instead.")) }, "Copy")) : null,
      !a ? h("p", null, "Loading...") : !a.apps.length ? h("p", { class: "hint" }, "No AI app is connected.") : h("ul", { class: "sources people" }, a.apps.map((x) => h("li", null,
        h("span", { class: "who" }, h("strong", null, x.app), h("span", { class: "hint" }, used(x))),
        h("button", { class: "link", disabled: state.busy, onclick: () => endSignIn("apps", x.id, "Disconnected") }, "Disconnect"))))));
}

function openAccount() {
  state.cardId = null; state.tab = "account"; state.asGiver = false; render();
  run(async () => { state.account = await api("/api/account"); });
}

function signOut() {
  run(async () => { await api("/api/signout", { method: "POST", body: {} }); location.assign("/signin"); });
}

function endSignIn(kind, id, said) {
  run(async () => { state.account = await api("/api/account/" + kind + "/" + encodeURIComponent(id), { method: "DELETE" }); toast(said); });
}

// Who is in a project. Only its receivers see this, since it shows addresses.
const peoplePath = (id) => "/api/projects/" + encodeURIComponent(id) + "/people";
const ROLE_NAMES = [["giver", "Giver"], ["receiver", "Receiver"]];

function peopleForm(p) {
  const d = state.people[p.id];
  const k = (f) => "people:" + p.id + ":" + f;
  const fresh = state.invited && state.invited.project_id === p.id ? state.invited : null;
  const roleSelect = (label, value, onchange) => h("select", { "aria-label": label, disabled: state.busy, onchange },
    ROLE_NAMES.map(([v, name]) => h("option", { value: v, selected: v === value }, name)));
  return h("section", { class: "panel", "data-people": p.id },
    h("h3", null, p.name + ": People"),
    h("p", null, "Givers send ideas and follow where they stand. Receivers also file and scope ideas, start builds and change these settings."),
    !d ? h("p", null, "Loading...") : h("ul", { class: "sources people" }, d.people.map((x) => h("li", null,
      h("span", { class: "who" }, h("strong", null, x.name + (x.you ? " (you)" : "")), h("span", { class: "hint" }, x.email)),
      h("span", { class: "row" },
        roleSelect("What " + x.name + " is in " + p.name, x.role, (e) => setRole(p, x, e.target.value)),
        h("button", { class: "link", disabled: state.busy, onclick: () => setRole(p, x, "none") }, x.you ? "Leave" : "Take out"))))),
    fresh ? h("div", { class: "callout" },
      h("h4", null, fresh.name + " is in"),
      h("p", null, "Nothing is sent to them. Pass this on yourself:"),
      h("code", { class: "key" }, fresh.text),
      state.me.sign_in === "access" ? h("p", { class: "hint" }, "This install signs people in through Cloudflare Access. Add their address to the Access policy as well, or the sign-in page will turn them away.") : null,
      state.me.sign_in === "builtin" ? h("p", { class: "hint" }, "They sign in with the account that has this address. Nothing else has to be set up for them.") : null,
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => navigator.clipboard.writeText(fresh.text).then(() => toast("Copied"), () => toast("Couldn't copy. Select it instead.")) }, "Copy"),
        h("button", { class: "link", onclick: () => { state.invited = null; render(); } }, "Done"))) : null,
    h("div", { class: "stack-form" },
      h("h4", null, "Add someone"),
      h("label", { class: "stack" }, h("span", null, "Name"),
        h("input", { maxlength: "80", autocomplete: "off", "aria-label": "Their name", value: state.drafts[k("name")] || "", oninput: (e) => { state.drafts[k("name")] = e.target.value; } })),
      h("label", { class: "stack" }, h("span", null, "Email address", h("span", { class: "hint" }, " The one they sign in with")),
        h("input", { type: "email", inputmode: "email", autocapitalize: "off", spellcheck: "false", autocomplete: "off", maxlength: "254", "aria-label": "Their email address", value: state.drafts[k("email")] || "", oninput: (e) => { state.drafts[k("email")] = e.target.value; } })),
      h("label", { class: "stack" }, h("span", null, "They are a"),
        roleSelect("What they are in " + p.name, state.drafts[k("role")] || "giver", (e) => { state.drafts[k("role")] = e.target.value; })),
      h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: () => addPerson(p, k) }, "Add"))),
    h("div", { class: "stack-form" },
      h("h4", null, "Project name"),
      h("div", { class: "row" },
        h("input", { maxlength: "60", "aria-label": "Project name", value: k("pname") in state.drafts ? state.drafts[k("pname")] : p.name, oninput: (e) => { state.drafts[k("pname")] = e.target.value; } }),
        h("button", { class: "quiet", disabled: state.busy, onclick: () => renameProject(p, k("pname")) }, "Save"))),
    d && d.changes.length ? h("details", null, h("summary", null, "Recent changes"),
      h("ul", { class: "changes" }, d.changes.map((x) => h("li", null, fmtDate(x.at) + ": " + x.by + " " + x.what)))) : null);
}

async function afterPeopleChange() {
  state.me = await api("/api/me");
  if (!state.me.projects.some((x) => x.role === "receiver")) { state.tab = "cards"; return; }
  for (const x of state.me.projects.filter((y) => y.role === "receiver")) if (!state.people[x.id]) state.people[x.id] = await api(peoplePath(x.id));
}

function setRole(p, x, role) {
  if (role === x.role) return;
  const ask = role === "none" ? (x.you ? "Leave " + p.name + "? You will no longer see its ideas." : "Take " + x.name + " out of " + p.name + "? They will no longer see its ideas.")
    : x.you ? "Become a giver in " + p.name + "? You will no longer be able to file ideas or change its settings." : null;
  if (ask && !window.confirm(ask)) { render(); return; }
  run(async () => {
    try {
      const r = await api(peoplePath(p.id), { method: "PUT", body: { email: x.email, role } });
      if (r.left) delete state.people[p.id]; else state.people[p.id] = r;
      await afterPeopleChange();
      toast(role === "none" ? (x.you ? "You left " + p.name : x.name + " was taken out") : x.name + " is now a " + role);
    } catch (e) { state.people[p.id] = await api(peoplePath(p.id)).catch(() => state.people[p.id]); throw e; }
  });
}

function addPerson(p, k) {
  const name = (state.drafts[k("name")] || "").trim(), email = (state.drafts[k("email")] || "").trim(), role = state.drafts[k("role")] || "giver";
  if (!email) { toast("Add their email address"); return; }
  run(async () => {
    state.people[p.id] = await api(peoplePath(p.id), { method: "PUT", body: { email, role, name } });
    const added = state.people[p.id].people.find((x) => x.email === email.toLowerCase());
    state.invited = { project_id: p.id, name: added ? added.name : name,
      text: "You've been added to " + p.name + " on Inrface. Open " + location.origin + " and sign in with " + email.toLowerCase() + "." };
    for (const f of ["name", "email", "role"]) delete state.drafts[k(f)];
    await afterPeopleChange();
  });
}

function renameProject(p, key) {
  const name = (state.drafts[key] ?? p.name).trim();
  if (!name || name === p.name) { toast("Nothing changed"); return; }
  run(async () => {
    await api("/api/projects/" + encodeURIComponent(p.id) + "/name", { method: "PUT", body: { name } });
    delete state.drafts[key];
    state.people[p.id] = await api(peoplePath(p.id));
    await afterPeopleChange();
    toast("Saved");
  });
}

function newProjectForm() {
  return h("section", { class: "panel", id: "newProject" },
    h("h3", null, "Start a project"),
    h("p", null, "A project has its own ideas, people and settings. You will be its first receiver, and can add the others from there."),
    h("div", { class: "row" },
      h("input", { maxlength: "60", placeholder: "Its name", "aria-label": "Name of the new project", value: state.drafts.newProject || "", oninput: (e) => { state.drafts.newProject = e.target.value; } }),
      h("button", { class: "quiet", disabled: state.busy, onclick: createProject }, "Create")));
}

function createProject() {
  const name = (state.drafts.newProject || "").trim();
  if (!name) { toast("Name it first"); return; }
  run(async () => {
    const made = await api("/api/projects", { method: "POST", body: { name } });
    delete state.drafts.newProject;
    state.me = await api("/api/me");
    await loadSettings();
    toast(made.name + " is ready");
  });
}

// How a build asked for from an AI app gets started. This is the person's own setting, not a project's.
function voiceForm() {
  return h("section", { class: "panel", id: "voiceBuilds" },
    h("h3", null, "Starting builds by voice"),
    h("p", null, "When you ask your AI app to start a build, nothing is sent until you press Start build on the idea here. You get an alert that opens it, so turn alerts on for this device."));
}


// Connectors: each kind describes its own settings, so one form serves them all.
const connPath = (id, rest) => "/api/projects/" + encodeURIComponent(id) + "/connectors" + (rest || "");

function connectorsForm(p) {
  const data = state.connectors[p.id];
  if (!data) return h("section", { class: "panel" }, h("h3", null, p.name + ": Connectors"), h("p", null, "Loading..."));
  const edit = state.connEdit && state.connEdit.project_id === p.id ? state.connEdit : null;
  const fresh = state.reveal && state.reveal.project_id === p.id ? state.reveal : null;
  const pick = "conn:" + p.id + ":pick";
  const missing = (x) => {
    const kind = data.kinds.find((k) => k.id === x.kind);
    return Object.keys(x.secrets).filter((k) => !x.secrets[k].set).map((k) => ((kind && kind.fields.find((f) => f.key === k)) || { label: k }).label.toLowerCase());
  };
  const filled = (v) => v.replaceAll("{origin}", location.origin);
  const copy = (v) => navigator.clipboard.writeText(v).then(() => toast("Copied"), () => toast("Couldn't copy. Select it instead."));
  const status = (x) => x.unreadable ? "its secrets can't be read; enter them again"
    : missing(x).length ? "not finished: it still needs its " + missing(x).join(" and ")
    : !x.enabled ? "off"
    : x.last_error ? "last try failed: " + x.last_error
    : x.last_ok_at ? "last worked " + fmtDate(x.last_ok_at) : "not tried yet";
  return h("section", { class: "panel" },
    h("h3", null, p.name + ": Connectors"),
    h("p", null, "Links to other tools: places Start build sends the scope alongside GitHub, and places ideas come in from. ",
      h("a", { href: "/docs/connectors", target: "_blank", rel: "noopener" }, "How connectors work")),
    data.encrypted ? null : h("p", { class: "hint" }, "Secrets saved here aren't encrypted on the server yet. Set INNERFACE_SECRET_KEY to turn that on."),
    fresh ? h("div", { class: "callout" },
      h("h4", null, fresh.label + " for " + fresh.name),
      h("p", null, "Copy it now. It won't be shown again."),
      h("code", { class: "key" }, fresh.value),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => copy(fresh.value) }, "Copy"),
        h("button", { class: "link", onclick: () => { state.reveal = null; render(); } }, "Done"))) : null,
    data.connectors.length ? h("ul", { class: "sources connectors" }, data.connectors.map((x) => x.kind === "slack" && !x.last_ok_at && !x.unreadable ? slackSteps(p, x, copy, filled) : h("li", null,
      h("span", null, h("strong", null, x.name), x.name === x.kind_label ? "" : " (" + x.kind_label + ")", ", " + status(x)),
      h("span", { class: "row" },
        h("button", { class: "link", disabled: state.busy || !x.enabled || x.unreadable, onclick: () => testConnector(p.id, x) }, "Test"),
        h("button", { class: "link", disabled: state.busy, onclick: () => { state.connEdit = { project_id: p.id, id: x.id, kind: x.kind }; render(); } }, "Edit"),
        h("button", { class: "link", disabled: state.busy, onclick: () => saveConnector(p.id, x.id, { enabled: !x.enabled }, x.enabled ? "Turned off" : "Turned on") }, x.enabled ? "Turn off" : "Turn on"),
        h("button", { class: "link", disabled: state.busy, onclick: () => removeConnector(p.id, x) }, "Remove")),
      (x.notes || []).length ? h("span", { class: "notes" }, x.notes.map((n) => h("span", { class: "row" },
        n.show ? h("span", null, n.label + ": ", h("code", null, filled(n.value))) : null,
        h("button", { class: "link", onclick: () => copy(filled(n.value)) }, "Copy " + (n.show ? "it" : n.label.toLowerCase()))))) : null)))
      : h("p", { class: "hint" }, "No connectors yet."),
    edit ? connectorEditor(p, data, edit) : h("div", { class: "row" },
      h("select", { "aria-label": "Kind of connector", onchange: (e) => { state.drafts[pick] = e.target.value; } },
        data.kinds.map((k) => h("option", { value: k.id, selected: (state.drafts[pick] || data.kinds[0].id) === k.id }, k.label))),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => { state.connEdit = { project_id: p.id, id: null, kind: state.drafts[pick] || data.kinds[0].id }; render(); } }, "Add connector")));
}

// Slack takes a few trips between two apps, so it is done one step at a time. Each step shows once the one before it is
// done, and the Test button only appears at the end. What is done is read from what is saved, so it survives leaving the page.
function slackSteps(p, x, copy, filled) {
  const k = (f) => "slack:" + x.id + ":" + f;
  const set = (key) => !!(x.secrets[key] && x.secrets[key].set);
  const signing = set("signing_secret"), bot = set("bot_token");
  const made = signing || !!state.drafts[k("made")];
  const manifest = (x.notes || []).find((n) => /manifest/i.test(n.label));
  const step = !made ? 2 : !signing ? 3 : !bot ? 4 : 5;
  const done = (text) => h("p", { class: "stepdone" }, "\u2713 " + text);
  const saveSecret = (key, saved) => {
    const v = (state.drafts[k(key)] || "").trim();
    if (!v) { toast("Paste it first"); return; }
    run(async () => {
      await api(connPath(p.id, "/" + encodeURIComponent(x.id)), { method: "PUT", body: { values: { [key]: v } } });
      delete state.drafts[k(key)];
      await reloadConnectors(p.id);
      toast(saved);
    });
  };
  const secretBox = (key, label, placeholder, saved) => h("div", { class: "row" },
    h("input", { type: "password", autocomplete: "off", autocapitalize: "off", autocorrect: "off", spellcheck: "false", placeholder, "aria-label": label,
      value: state.drafts[k(key)] || "", oninput: (e) => { state.drafts[k(key)] = e.target.value; } }),
    h("button", { class: "primary", disabled: state.busy, onclick: () => saveSecret(key, saved) }, "Save"));
  return h("li", { class: "walk" },
    h("p", null, h("strong", null, x.name), x.name === x.kind_label ? ", " : " (Slack), ", "step " + step + " of 5"),
    done("Ideas channel " + (x.values.channel || "") + " saved"),
    step === 2 ? h("div", { class: "stack-form" },
      h("h4", null, "Create the Slack app"),
      h("ol", null,
        h("li", null, "Press ", h("strong", null, "Copy the app manifest"), " below."),
        h("li", null, "Open ", h("a", { href: "https://api.slack.com/apps", target: "_blank", rel: "noopener" }, "api.slack.com/apps"), ", press ", h("strong", null, "Create New App"), ", choose ", h("strong", null, "From a manifest"), ", and pick your workspace."),
        h("li", null, "Slack asks for the manifest in a box with two tabs. Choose ", h("strong", null, "YAML"), ", not JSON, then paste it. JSON is the tab it opens on."),
        h("li", null, "Press ", h("strong", null, "Next"), ", then ", h("strong", null, "Create"), ".")),
      h("p", { class: "hint" }, "Slack checks this server's address when it creates the app. If it can't reach it, creating fails and just says to try again. When this server sits behind Cloudflare Access, the path ", h("code", null, "hooks/slack"), " has to be opened first. The Slack guide says how."),
      h("div", { class: "row" },
        manifest ? h("button", { class: "primary", onclick: () => copy(filled(manifest.value)) }, "Copy the app manifest (YAML)") : null,
        h("button", { class: "quiet", onclick: () => { state.drafts[k("made")] = true; render(); } }, "I've created the app"))) : done("Slack app created"),
    step === 3 ? h("div", { class: "stack-form" },
      h("h4", null, "Paste its signing secret"),
      h("p", null, "In the Slack app, open ", h("strong", null, "Basic Information"), " in the left menu and scroll to ", h("strong", null, "App Credentials"), ". Press ", h("strong", null, "Show"), " next to ", h("strong", null, "Signing Secret"), " and copy it."),
      secretBox("signing_secret", "Signing secret", "Signing secret", "Saved")) : step > 3 ? done("Signing secret saved") : null,
    step === 4 ? h("div", { class: "stack-form" },
      h("h4", null, "Install the app and paste its token"),
      h("p", null, "In the Slack app, open ", h("strong", null, "Install App"), " in the left menu, press ", h("strong", null, "Install to Workspace"), " and then ", h("strong", null, "Allow"), ". Copy the ", h("strong", null, "Bot User OAuth Token"), ". It starts with ", h("code", null, "xoxb-"), "."),
      secretBox("bot_token", "Bot token", "xoxb-...", "Saved")) : step > 4 ? done("Bot token saved") : null,
    step === 5 ? h("div", { class: "stack-form" },
      h("h4", null, "Add it to the channel, then test"),
      h("p", null, "In the ideas channel in Slack, type ", h("code", null, "/invite @Inrface"), " and pick the app. The app has to be in the channel, even a public one, or it can't reply there."),
      x.last_error ? h("p", { class: "hint" }, "Last try: " + x.last_error) : null,
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: state.busy || !x.enabled, onclick: () => testConnector(p.id, x) }, "Test it"))) : null,
    h("p", { class: "row" },
      h("button", { class: "link", disabled: state.busy, onclick: () => { state.connEdit = { project_id: p.id, id: x.id, kind: x.kind }; render(); } }, "Edit settings"),
      h("button", { class: "link", disabled: state.busy, onclick: () => removeConnector(p.id, x) }, "Remove")));
}

function connectorEditor(p, data, edit) {
  const kind = data.kinds.find((k) => k.id === edit.kind);
  const saved = edit.id ? data.connectors.find((x) => x.id === edit.id) : null;
  if (!kind || (edit.id && !saved)) return null;
  const k = (f) => "conn:" + p.id + ":" + (edit.id || "new") + ":" + f;
  const val = (f, fallback) => k(f) in state.drafts ? state.drafts[k(f)] : fallback;
  const typed = kind.fields.filter((f) => !f.generated && !(kind.id === "slack" && !saved && f.secret));
  const made = kind.fields.filter((f) => f.generated);
  return h("div", { class: "stack-form" },
    h("h4", null, (saved ? "Edit " + saved.name : "New " + kind.label + " connector")),
    h("p", { class: "hint" }, kind.summary + " ", kind.docs ? h("a", { href: kind.docs, target: "_blank", rel: "noopener" }, "Setup guide") : null),
    h("label", { class: "stack" }, h("span", null, "Name"),
      h("input", { value: val("name", saved ? saved.name : ""), placeholder: kind.label, "aria-label": "Connector name", oninput: (e) => { state.drafts[k("name")] = e.target.value; } })),
    typed.map((f) => {
      const isSet = saved && f.secret && saved.secrets[f.key] && saved.secrets[f.key].set;
      return h("label", { class: "stack" }, h("span", null, f.label + (f.required ? "" : " (optional)")),
        h("input", { type: f.secret ? "password" : "text", autocomplete: "off", autocapitalize: "off", autocorrect: "off", spellcheck: "false", "aria-label": f.label,
          value: val(f.key, f.secret ? "" : (saved ? saved.values[f.key] || "" : "")),
          placeholder: isSet ? "Leave blank to keep the one ending in " + saved.secrets[f.key].hint : f.placeholder,
          oninput: (e) => { state.drafts[k(f.key)] = e.target.value; } }),
        f.help ? h("span", { class: "hint" }, f.help) : null);
    }),
    kind.id === "slack" && !saved ? h("p", { class: "hint" }, "That is the first of five steps. After this one you make the Slack app, paste two things from it, and add it to the channel.") : null,
    made.length && !saved ? h("p", { class: "hint" }, "A " + made.map((f) => f.label.toLowerCase()).join(" and ") + " is made for you and shown once after you save.") : null,
    h("div", { class: "row" },
      h("button", { class: "primary", disabled: state.busy, onclick: () => submitConnector(p.id, edit, kind, k) }, saved ? "Save" : "Add"),
      h("button", { class: "link", onclick: () => { state.connEdit = null; render(); } }, "Cancel"),
      saved ? made.map((f) => h("button", { class: "link", disabled: state.busy, onclick: () => regenerateConnector(p.id, saved, f) }, "New " + f.label.toLowerCase())) : null));
}

async function reloadConnectors(id) {
  state.connectors[id] = await api(connPath(id));
  state.me = await api("/api/me");
}

function showReveal(id, r, kind) {
  const key = Object.keys(r.reveal || {})[0];
  if (!key) return;
  const f = (kind.fields || []).find((x) => x.key === key);
  state.reveal = { project_id: id, name: r.name, label: f ? f.label : "Secret", value: r.reveal[key] };
}

function submitConnector(id, edit, kind, k) {
  const body = { values: {} };
  if (state.drafts[k("name")] !== undefined) body.name = state.drafts[k("name")];
  for (const f of kind.fields) if (!f.generated && state.drafts[k(f.key)] !== undefined) body.values[f.key] = state.drafts[k(f.key)];
  if (!edit.id) body.kind = kind.id;
  else if (!Object.keys(body.values).length) delete body.values;
  if (edit.id && body.name === undefined && !body.values) { state.connEdit = null; render(); return; }
  run(async () => {
    const r = await api(connPath(id, edit.id ? "/" + encodeURIComponent(edit.id) : ""), { method: edit.id ? "PUT" : "POST", body });
    for (const key of Object.keys(state.drafts)) if (key.startsWith(k(""))) delete state.drafts[key];
    state.connEdit = null;
    showReveal(id, r, kind);
    await reloadConnectors(id);
    toast(edit.id ? "Saved. Tap Test to try it." : "Added. Tap Test to try it.");
  });
}

function saveConnector(id, cid, body, done) {
  run(async () => {
    await api(connPath(id, "/" + encodeURIComponent(cid)), { method: "PUT", body });
    await reloadConnectors(id);
    toast(done);
  });
}

function regenerateConnector(id, x, f) {
  if (!window.confirm("Make a new " + f.label.toLowerCase() + " for " + x.name + "? The old one stops working right away.")) return;
  run(async () => {
    const r = await api(connPath(id, "/" + encodeURIComponent(x.id)), { method: "PUT", body: { regenerate: true } });
    const kind = state.connectors[id].kinds.find((k) => k.id === x.kind) || {};
    state.connEdit = null;
    showReveal(id, r, kind);
    await reloadConnectors(id);
  });
}

function testConnector(id, x) {
  run(async () => {
    try {
      const r = await api(connPath(id, "/" + encodeURIComponent(x.id) + "/test"), { method: "POST", body: {} });
      toast(r.detail);
    } finally {
      state.connectors[id] = await api(connPath(id));
    }
  });
}

function removeConnector(id, x) {
  if (!window.confirm("Remove " + x.name + "? Builds stop going there.")) return;
  run(async () => {
    await api(connPath(id, "/" + encodeURIComponent(x.id)), { method: "DELETE" });
    if (state.connEdit && state.connEdit.id === x.id) state.connEdit = null;
    await reloadConnectors(id);
    toast("Removed");
  });
}

function githubForm(p) {
  const g = state.github[p.id];
  const k = (f) => "gh:" + p.id + ":" + f;
  const val = (f, fallback) => k(f) in state.drafts ? state.drafts[k(f)] : fallback;
  if (!g) return h("section", { class: "panel" }, h("h3", null, p.name), h("p", null, "Loading..."));
  const tokenNote = !g.token_set ? "No token yet."
    : g.token_source === "server" ? "Using the token from the server's settings file."
    : "A token ending in " + g.token_hint + " is saved.";
  return h("section", { class: "panel" },
    h("h3", null, p.name + ": GitHub"),
    h("p", null, "Start build opens an issue in this repo with the build label. Your coding agent there picks it up."),
    h("div", { class: "stack-form" },
      h("label", { class: "stack" }, h("span", null, "Repo (owner/name)"),
        h("input", { value: val("repo", g.repo), placeholder: "owner/name", autocapitalize: "off", autocorrect: "off", spellcheck: "false", "aria-label": "Repo", oninput: (e) => { state.drafts[k("repo")] = e.target.value; } })),
      h("label", { class: "stack" }, h("span", null, "Build label"),
        h("input", { value: val("build_label", g.build_label), autocapitalize: "off", "aria-label": "Build label", oninput: (e) => { state.drafts[k("build_label")] = e.target.value; } })),
      h("label", { class: "stack" }, h("span", null, "Token"),
        h("input", { type: "password", autocomplete: "off", value: val("token", ""), placeholder: g.token_set ? "Leave blank to keep the saved one" : "github_pat_...", "aria-label": "GitHub token", oninput: (e) => { state.drafts[k("token")] = e.target.value; } })),
      h("p", { class: "hint" }, tokenNote + " Use a fine-grained token for just this repo, with Contents, Issues and Pull requests all set to Read and write. The build runner uses the same token."),
      h("label", { class: "stack" }, h("span", null, "Who can ask their AI app about the code"),
        h("select", { "aria-label": "Who can ask about the code", onchange: (e) => { state.drafts[k("code_access")] = e.target.value; } },
          [["everyone", "Everyone in the project"], ["receivers", "Only people who review ideas"], ["off", "Nobody"]]
            .map(([v, label]) => h("option", { value: v, selected: v === val("code_access", g.code_access) }, label)))),
      h("p", { class: "hint" }, "Their AI app can search and read this repo to check whether an idea is already built or would clash with something. Anything committed to the repo can be read this way, apart from files that look like they hold passwords or keys."),
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: state.busy, onclick: () => saveGitHub(p.id, k) }, "Save"),
        h("button", { class: "quiet", disabled: state.busy || !g.repo || !g.token_set, onclick: () => checkGitHub(p.id) }, "Check connection"),
        g.token_source === "app" ? h("button", { class: "link", disabled: state.busy, onclick: () => clearGitHubToken(p.id) }, "Remove saved token") : null)));
}

// The names a team gives the places an idea can sit. A label is only a name: each counts as one built-in status.
function labelsForm(p) {
  const key = "labels:" + p.id;
  const rows = state.drafts[key] || p.labels.map((x) => ({ ...x }));
  const change = (fn) => { state.drafts[key] = rows.map((x) => ({ ...x })); fn(state.drafts[key]); render(); };
  return h("section", { class: "panel", "data-labels": p.id },
    h("h3", null, p.name + ": Board labels"),
    h("p", null, "Name the places an idea can sit, in the order your team works. Add a label for a sprint, a review step, a stage you test in, whatever your team uses. They show as the board's columns."),
    h("p", { class: "hint" }, "Each label counts as one of a few core meanings, which is what the app goes by: New, Needs info, Roadmap, Someday (possible, but far off the roadmap), Active development, Testing, In production and Rejected. Start build moves an idea into Active development, and an idea in production is finished, whatever you call the label."),
    h("ul", { class: "sources" }, rows.map((x, i) => h("li", null,
      h("input", { value: x.name, maxlength: "30", "aria-label": "Label " + (i + 1) + " name", oninput: (e) => { rows[i].name = e.target.value; state.drafts[key] = rows; } }),
      h("select", { "aria-label": "Label " + (i + 1) + " counts as", onchange: (e) => { rows[i].kind = e.target.value; state.drafts[key] = rows; } },
        state.me.kinds.map((k) => h("option", { value: k, selected: k === x.kind }, "Counts as " + k))),
      h("button", { class: "link", disabled: i === 0, "aria-label": "Move up", onclick: () => change((r) => { [r[i - 1], r[i]] = [r[i], r[i - 1]]; }) }, "Up"),
      h("button", { class: "link", disabled: i === rows.length - 1, "aria-label": "Move down", onclick: () => change((r) => { [r[i + 1], r[i]] = [r[i], r[i + 1]]; }) }, "Down"),
      h("button", { class: "link", "aria-label": "Remove label", onclick: () => change((r) => { r.splice(i, 1); }) }, "Remove")))),
    h("div", { class: "row" },
      h("button", { class: "quiet", disabled: rows.length >= 14, onclick: () => change((r) => { r.push({ name: "", kind: "Roadmap" }); }) }, "Add a label"),
      h("button", { class: "primary", disabled: state.busy, onclick: () => saveLabels(p.id, key, rows) }, "Save"),
      h("button", { class: "link", disabled: state.busy, onclick: () => saveLabels(p.id, key, null) }, "Put back the usual ones")));
}

function saveLabels(id, key, rows) {
  if (rows === null && !window.confirm("Put back the usual labels? Ideas keep their meaning but lose any custom label.")) return;
  run(async () => {
    await api("/api/projects/" + encodeURIComponent(id) + "/labels", { method: "PUT", body: { labels: rows === null ? null : rows.map((x) => ({ name: x.name, kind: x.kind })) } });
    delete state.drafts[key];
    state.me = await api("/api/me");
    await refresh();
    toast("Saved");
  });
}

// How code travels from a merged pull request to live. Each stage is judged by a branch picked from the ones GitHub shows.
const flowPath = (id) => "/api/projects/" + encodeURIComponent(id) + "/flow";

function flowForm(p) {
  const f = state.flow[p.id];
  const key = "flow:" + p.id;
  if (!f) return h("section", { class: "panel" }, h("h3", null, p.name + ": Build flow"), h("p", null, "Loading..."));
  const rows = state.drafts[key] || f.stages.map((x) => ({ ...x }));
  const change = (fn) => { state.drafts[key] = rows.map((x) => ({ ...x })); fn(state.drafts[key]); render(); };
  const seen = f.branches;
  return h("section", { class: "panel", "data-flow": p.id },
    h("h3", null, p.name + ": Build flow"),
    h("p", null, "Tell Inrface how your code travels once it is built, and each idea will show where it has got to. Add a stage for each step, in order, and pick the branch that marks it. The last stage is live: an idea becomes Done / Live when its code gets there."),
    h("p", { class: "hint" }, "Not sure how to set this up? ", h("a", { href: "/docs/build-flow", target: "_blank", rel: "noopener" }, "See example setups"), ", such as straight to main, a dev branch in front, or dev, staging and main."),
    h("p", { class: "hint" }, "An idea reaches a stage when its merged pull request is on that branch. A branch that gets the code by a squash merge of a pull request, rather than by merging the branch, isn't recognised. Leave the list empty and an idea is done when its issue closes with the pull request merged."),
    f.problem ? h("div", { class: "callout" }, h("h4", null, "Can't see the branches"), h("p", null, f.problem)) : null,
    rows.length ? h("ol", { class: "sources" }, rows.map((x, i) => h("li", null,
      h("input", { value: x.name, maxlength: "40", placeholder: i === rows.length - 1 ? "e.g. Live" : "e.g. Testing", "aria-label": "Stage " + (i + 1) + " name", oninput: (e) => { rows[i].name = e.target.value; state.drafts[key] = rows; } }),
      h("select", { "aria-label": "Stage " + (i + 1) + " branch", disabled: !seen, onchange: (e) => { rows[i].branch = e.target.value; state.drafts[key] = rows; } },
        h("option", { value: "", selected: !x.branch }, "Pick a branch"),
        (seen || []).concat(x.branch && seen && !seen.includes(x.branch) ? [x.branch] : x.branch && !seen ? [x.branch] : [])
          .map((b) => h("option", { value: b, selected: b === x.branch }, b + (seen && !seen.includes(b) ? " (not found)" : "")))),
      h("button", { class: "link", disabled: i === 0, "aria-label": "Move up", onclick: () => change((r) => { [r[i - 1], r[i]] = [r[i], r[i - 1]]; }) }, "Up"),
      h("button", { class: "link", disabled: i === rows.length - 1, "aria-label": "Move down", onclick: () => change((r) => { [r[i + 1], r[i]] = [r[i], r[i + 1]]; }) }, "Down"),
      h("button", { class: "link", "aria-label": "Remove stage", onclick: () => change((r) => { r.splice(i, 1); }) }, "Remove"))))
      : h("p", { class: "hint" }, "No stages yet."),
    h("div", { class: "row" },
      h("button", { class: "quiet", disabled: !seen || rows.length >= 6, onclick: () => change((r) => { r.push({ name: "", branch: "" }); }) }, "Add a stage"),
      h("button", { class: "primary", disabled: state.busy, onclick: () => saveFlow(p.id, key, rows) }, "Save")));
}

function saveFlow(id, key, rows) {
  run(async () => {
    state.flow[id] = await api(flowPath(id), { method: "PUT", body: { stages: rows.map((x) => ({ name: x.name, branch: x.branch })) } });
    delete state.drafts[key];
    toast("Saved");
  });
}

// The script that picks up build issues runs on the team's own machine. Here: when it checks, how many builds at once, and its key.
const runnerPath = (id, rest = "") => "/api/projects/" + encodeURIComponent(id) + "/runner" + rest;

function runnerForm(p) {
  const r = state.runner[p.id];
  const k = (f) => "runner:" + p.id + ":" + f;
  const val = (f, fallback) => k(f) in state.drafts ? state.drafts[k(f)] : fallback;
  if (!r) return h("section", { class: "panel" }, h("h3", null, p.name), h("p", null, "Loading..."));
  const fresh = state.runnerKey && state.runnerKey.project_id === p.id ? state.runnerKey : null;
  const g = state.github[p.id] || {};
  const cmd = runnerCommand(g.repo);
  return h("section", { class: "panel", "data-runner": p.id },
    h("h3", null, p.name + ": Build runner"),
    h("p", null, "The runner picks up each build and hands it to your coding agent on the machine that holds your code. It pushes the agent's work and opens the pull request itself. It never merges. ",
      h("a", { href: "/docs/build-runner", target: "_blank", rel: "noopener" }, "Full guide")),
    h("ol", { class: "steps" },
      h("li", null, h("strong", null, "One GitHub token. "),
        "The runner uses the same token as this project. In GitHub, give it ", h("strong", null, "Contents"), ", ", h("strong", null, "Issues"), " and ", h("strong", null, "Pull requests"),
        ", all set to Read and write. You can change a token's permissions without making a new one.",
        g.token_set ? null : h("span", { class: "hint" }, " This project has no token yet: add one under GitHub and build flow, above.")),
      h("li", null, h("strong", null, "Paste this on the machine with your code. "),
        cmd ? [
          "It downloads the runner, asks for the token once (nothing shows as you paste it), uses your checkout of " + g.repo + " if you run it inside one or clones it, and starts it in the background.",
          copyBox(cmd, "Copy command"),
          h("p", { class: "hint" }, "No internet access from that machine to GitHub's file server? ",
            h("a", { href: "/runner/inrface_runner.py", download: "inrface_runner.py" }, "Download the script"),
            ", copy it there, and run ", h("code", null, "python3 inrface_runner.py setup --repo " + g.repo + " && python3 inrface_runner.py install --repo " + g.repo), ".")]
          : h("span", { class: "hint" }, "Link a GitHub repo under GitHub and build flow, above, and the command appears here.")),
      h("li", null, h("strong", null, "That's it. "), "Press Start build on an idea and the runner picks it up within five minutes. ",
        "To watch it: ", h("code", null, "journalctl --user -u " + runnerService(g.repo) + " -f"), ". To stop it: ", h("code", null, "systemctl --user disable --now " + runnerService(g.repo)), ". ",
        "Each project gets its own runner, so one machine can build several projects.")),
    h("h4", null, "Optional: switch it on and off from here"),
    h("p", { class: "hint" }, "Without this the runner works from its own settings on the machine. With a key, the settings below decide whether it runs, how often it checks and how many builds go at once. "
      + (state.me.sign_in === "access" ? "Cloudflare Access is in front of this app, so the runner also needs an Access service token, or a bypass for /api/runner/v1/config. See the full guide." : "")),
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: val("enabled", r.enabled), "aria-label": "Runner on", onchange: (e) => { state.drafts[k("enabled")] = e.target.checked; } }),
      " Switched on. When this is off, the script checks in and does nothing."),
    h("div", { class: "stack-form" },
      h("label", { class: "stack" }, h("span", null, "Check for new build issues every (minutes)"),
        h("input", { type: "number", min: r.limits.interval_minutes[0], max: r.limits.interval_minutes[1], value: val("interval", r.interval_minutes), "aria-label": "Check every minutes", oninput: (e) => { state.drafts[k("interval")] = e.target.value; } })),
      h("label", { class: "stack" }, h("span", null, "Builds at the same time"),
        h("input", { type: "number", min: 1, max: r.limits.max_builds[1], value: val("builds", r.max_builds), "aria-label": "Builds at once", oninput: (e) => { state.drafts[k("builds")] = e.target.value; } }))),
    h("div", { class: "row" }, h("button", { class: "primary", disabled: state.busy, onclick: () => saveRunner(p.id, k) }, "Save")),
    h("h4", null, "Runner key"),
    fresh ? h("div", { class: "callout" },
      h("p", null, "Copy it now into the runner's file. It won't be shown again."),
      h("code", { class: "key" }, fresh.key),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => navigator.clipboard.writeText(fresh.key).then(() => toast("Copied"), () => toast("Couldn't copy. Select it instead.")) }, "Copy key"),
        h("button", { class: "link", onclick: () => { state.runnerKey = null; render(); } }, "Done"))) : null,
    h("p", { class: "hint" }, r.key_set ? "A key ending in " + r.key_hint + " is saved" + (r.last_seen_at ? ". The runner last checked in " + fmtDate(r.last_seen_at) + "." : ". The runner hasn't checked in yet.") : "No key yet. The key only lets the runner read these settings and the repo and label above. It can't read ideas.") ,
    h("div", { class: "row" },
      h("button", { class: "quiet", disabled: state.busy, onclick: () => makeRunnerKey(p.id) }, r.key_set ? "Make a new key" : "Make a key"),
      r.key_set ? h("button", { class: "link", disabled: state.busy, onclick: () => dropRunnerKey(p.id) }, "Remove key") : null));
}

// One line that downloads the runner, sets it up for the repo and keeps it running.
function runnerCommand(repo) {
  if (!repo) return "";
  const f = "~/.local/share/inrface-runner/inrface_runner.py";
  return "mkdir -p ~/.local/share/inrface-runner && curl -fsSL https://raw.githubusercontent.com/Inrface/Inrface/main/runner/inrface_runner.py -o " + f
    + " && python3 " + f + " setup --repo " + repo + " && python3 " + f + " install --repo " + repo;
}

// The systemd service the runner for this repo runs as (the same name the script gives it).
function runnerService(repo) {
  const name = String(repo || "").split("/").pop().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  return "inrface-runner-" + (name || "repo");
}

function copyBox(text, label) {
  return h("div", { class: "copybox" },
    h("code", { class: "key" }, text),
    h("button", { class: "primary", onclick: () => navigator.clipboard.writeText(text).then(() => toast("Copied. Paste it in a terminal on that machine."), () => toast("Couldn't copy. Select it instead.")) }, label || "Copy"));
}

function saveRunner(id, k) {
  const body = {};
  if (k("enabled") in state.drafts) body.enabled = !!state.drafts[k("enabled")];
  if (k("interval") in state.drafts) body.interval_minutes = parseInt(state.drafts[k("interval")], 10);
  if (k("builds") in state.drafts) body.max_builds = parseInt(state.drafts[k("builds")], 10);
  if (!Object.keys(body).length) { toast("Nothing changed"); return; }
  run(async () => {
    state.runner[id] = await api(runnerPath(id), { method: "PUT", body });
    for (const f of ["enabled", "interval", "builds"]) delete state.drafts[k(f)];
    toast("Saved");
  });
}

function makeRunnerKey(id) {
  if (state.runner[id].key_set && !window.confirm("Make a new key? The old one stops working at once.")) return;
  run(async () => {
    const r = await api(runnerPath(id, "/key"), { method: "POST" });
    state.runnerKey = { project_id: id, key: r.key };
    delete r.key;
    state.runner[id] = r;
  });
}

function dropRunnerKey(id) {
  if (!window.confirm("Remove the key? The runner stops checking in until you make a new one.")) return;
  run(async () => { state.runner[id] = await api(runnerPath(id, "/key"), { method: "DELETE" }); toast("Removed"); });
}

function sourcesForm(p) {
  const list = state.sources[p.id];
  const k = "src:" + p.id;
  const fresh = state.newKey && state.newKey.project_id === p.id ? state.newKey : null;
  return h("section", { class: "panel" },
    h("h3", null, p.name + ": Sources"),
    h("p", null, "Other systems, like your website, can send feature ideas and tool ratings here. Each gets its own key. ",
      h("a", { href: "/docs/intake-api", target: "_blank", rel: "noopener" }, "API reference")),
    fresh ? h("div", { class: "callout" },
      h("h4", null, "Key for " + fresh.name),
      h("p", null, "Copy it now. It won't be shown again."),
      h("code", { class: "key" }, fresh.key),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => navigator.clipboard.writeText(fresh.key).then(() => toast("Copied"), () => toast("Couldn't copy. Select it instead.")) }, "Copy key"),
        h("button", { class: "link", onclick: () => { state.newKey = null; render(); } }, "Done"))) : null,
    list ? (list.length ? h("ul", { class: "sources" }, list.map((x) => h("li", null,
      h("span", null, h("strong", null, x.name), " ····" + x.key_hint + (x.revoked ? " (revoked)" : x.last_used_at ? ", last used " + fmtDate(x.last_used_at) : ", never used")),
      x.revoked ? null : h("button", { class: "link", disabled: state.busy, onclick: () => revokeSource(p.id, x) }, "Revoke"))))
      : h("p", { class: "hint" }, "No sources yet.")) : null,
    h("div", { class: "row" },
      h("input", { placeholder: "Name, e.g. Website", "aria-label": "New source name", value: state.drafts[k] || "", oninput: (e) => { state.drafts[k] = e.target.value; } }),
      h("button", { class: "quiet", disabled: state.busy, onclick: () => createSource(p.id, k) }, "Create key")));
}

// Where people try what was built, and links that show the roadmap to someone without a login.
const SHARE_DAYS = [[30, "30 days"], [7, "7 days"], [90, "90 days"], [0, "Until I switch it off"]];

function shareForm(p) {
  const list = state.share[p.id];
  const k = (f) => "share:" + p.id + ":" + f;
  const fresh = state.newShare && state.newShare.project_id === p.id ? state.newShare : null;
  const g = state.github[p.id];
  return h("section", { class: "panel", "data-share": p.id },
    h("h3", null, p.name + ": After it ships"),
    h("h4", null, "Where people can try it"),
    h("p", null, "When an idea goes live, the person who had it is told, with this link. You can set a different one on any idea."),
    h("div", { class: "row" },
      h("input", { type: "url", inputmode: "url", autocapitalize: "off", spellcheck: "false", placeholder: "https://…", "aria-label": "Where people can try it",
        value: k("live") in state.drafts ? state.drafts[k("live")] : g ? g.live_url : "", oninput: (e) => { state.drafts[k("live")] = e.target.value; } }),
      h("button", { class: "quiet", disabled: state.busy || !g, onclick: () => saveLiveUrl(p.id, k("live")) }, "Save")),
    !can("share_links") ? null : [
    h("h4", null, "Roadmap links"),
    h("p", null, "A link that shows this project's roadmap to someone without a login, such as a client or an investor. It is read-only."),
    h("p", { class: "hint" }, "It shows the titles of ideas that are on the roadmap, being built or shipped in the last 90 days. It never shows who an idea came from, the conversation, the scope, the code or anything not yet filed. Anyone who has the link can open it until you switch it off."),
    fresh ? h("div", { class: "callout" },
      h("h4", null, "Link" + (fresh.label ? " for " + fresh.label : "")),
      h("p", null, "Copy it now. It won't be shown again."),
      h("code", { class: "key" }, location.origin + fresh.path),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => navigator.clipboard.writeText(location.origin + fresh.path).then(() => toast("Copied"), () => toast("Couldn't copy. Select it instead.")) }, "Copy link"),
        h("a", { class: "try", href: fresh.path, target: "_blank", rel: "noopener noreferrer" }, "See what it shows"),
        h("button", { class: "link", onclick: () => { state.newShare = null; render(); } }, "Done"))) : null,
    list ? (list.length ? h("ul", { class: "sources" }, list.map((x) => h("li", null,
      h("span", null, h("strong", null, x.label || "Roadmap link"), ", made " + fmtDate(x.created_at)
        + (x.summaries ? ", with summaries" : ", titles only")
        + (!x.working ? ", run out" : x.expires_at ? ", works until " + fmtDate(x.expires_at) : ", no end date")
        + (x.opens ? ", opened " + plural(x.opens, "time") : ", not opened yet")),
      h("button", { class: "link", disabled: state.busy, onclick: () => revokeShare(p.id, x) }, x.working ? "Switch off" : "Remove"))))
      : h("p", { class: "hint" }, "No links yet.")) : null,
    h("div", { class: "stack-form" },
      h("label", { class: "stack" }, h("span", null, "Who it's for"),
        h("input", { placeholder: "A name to remember it by", maxlength: "60", "aria-label": "Who the link is for", value: state.drafts[k("label")] || "", oninput: (e) => { state.drafts[k("label")] = e.target.value; } })),
      h("label", { class: "stack" }, h("span", null, "How long it works"),
        h("select", { "aria-label": "How long the link works", onchange: (e) => { state.drafts[k("days")] = e.target.value; } },
          SHARE_DAYS.map(([v, label]) => h("option", { value: String(v), selected: String(v) === String(state.drafts[k("days")] ?? 30) }, label)))),
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: !!state.drafts[k("sum")], onchange: (e) => { state.drafts[k("sum")] = e.target.checked; } }),
        h("span", null, "Show the one-line summary under each title")),
      h("div", { class: "row" }, h("button", { class: "quiet", disabled: state.busy, onclick: () => createShare(p.id, k) }, "Create link")))]);
}

const sharePath = (id, rest) => "/api/projects/" + encodeURIComponent(id) + "/share" + (rest || "");

function saveLiveUrl(id, key) {
  if (state.drafts[key] === undefined) { toast("Nothing changed"); return; }
  run(async () => {
    await afterGitHubChange(id, await api("/api/projects/" + encodeURIComponent(id) + "/github", { method: "PUT", body: { live_url: state.drafts[key].trim() } }));
    delete state.drafts[key];
    toast("Saved");
  });
}

function createShare(id, k) {
  run(async () => {
    const r = await api(sharePath(id), { method: "POST", body: { label: (state.drafts[k("label")] || "").trim(), days: Number(state.drafts[k("days")] ?? 30), summaries: !!state.drafts[k("sum")] } });
    state.newShare = { project_id: id, label: r.label, path: r.path };
    for (const f of ["label", "days", "sum"]) delete state.drafts[k(f)];
    state.share[id] = (await api(sharePath(id))).links;
  });
}

function revokeShare(id, x) {
  if (x.working && !window.confirm("Switch off this link? Anyone who has it will stop seeing the roadmap.")) return;
  run(async () => {
    await api(sharePath(id, "/" + encodeURIComponent(x.id) + "/revoke"), { method: "POST", body: {} });
    state.share[id] = (await api(sharePath(id))).links;
    toast("Switched off");
  });
}

function createSource(id, k) {
  const name = (state.drafts[k] || "").trim();
  if (!name) { toast("Name it first"); return; }
  run(async () => {
    const r = await api("/api/projects/" + encodeURIComponent(id) + "/sources", { method: "POST", body: { name } });
    state.newKey = { project_id: id, name: r.name, key: r.key };
    state.drafts[k] = "";
    state.sources[id] = (await api("/api/projects/" + encodeURIComponent(id) + "/sources")).sources;
  });
}

function revokeSource(id, x) {
  if (!window.confirm("Revoke the key for " + x.name + "? It stops working right away.")) return;
  run(async () => {
    await api("/api/projects/" + encodeURIComponent(id) + "/sources/" + encodeURIComponent(x.id) + "/revoke", { method: "POST", body: {} });
    state.sources[id] = (await api("/api/projects/" + encodeURIComponent(id) + "/sources")).sources;
    toast("Revoked");
  });
}

function openSettings() {
  try { state.settingsTab = localStorage.getItem("innerface.settingsTab") || state.settingsTab; } catch (_) {}
  state.cardId = null; state.tab = "settings"; state.asGiver = false; render();
  run(loadSettings);
}

async function loadSettings() {
  {
    for (const p of state.me.projects.filter((x) => x.role === "receiver")) {
      state.people[p.id] = await api(peoplePath(p.id));
      state.github[p.id] = await api("/api/projects/" + encodeURIComponent(p.id) + "/github");
      state.flow[p.id] = await api(flowPath(p.id));
      state.sources[p.id] = (await api("/api/projects/" + encodeURIComponent(p.id) + "/sources")).sources;
      state.connectors[p.id] = await api(connPath(p.id));
      state.runner[p.id] = await api(runnerPath(p.id));
      if (can("share_links")) state.share[p.id] = (await api(sharePath(p.id))).links;
    }
  }
}

async function afterGitHubChange(id, g) {
  state.github[id] = g;
  state.flow[id] = await api(flowPath(id));
  state.me = await api("/api/me");
}

const GH_FIELDS = ["repo", "build_label", "token", "code_access"];

function saveGitHub(id, k) {
  const body = {};
  for (const f of GH_FIELDS) if (state.drafts[k(f)] !== undefined) body[f] = state.drafts[k(f)];
  if (!Object.keys(body).length) { toast("Nothing changed"); return; }
  run(async () => {
    await afterGitHubChange(id, await api("/api/projects/" + encodeURIComponent(id) + "/github", { method: "PUT", body }));
    for (const f of GH_FIELDS) delete state.drafts[k(f)];
    toast("Saved. Tap Check connection to test it.");
  });
}

function checkGitHub(id) {
  run(async () => {
    const r = await api("/api/projects/" + encodeURIComponent(id) + "/github/check", { method: "POST", body: {} });
    const code = r.code === "yes" ? " The code can be read." : r.code === "empty" ? " The repo has no code yet."
      : " The token can't read the code: give it Contents read-only so people can ask about what's built.";
    toast("Connected to " + r.repo + (r.label === "created" ? ". Created the build label." : ". Build label is there.") + code);
  });
}

function clearGitHubToken(id) {
  if (!window.confirm("Remove the saved token? Builds stop until you add another one.")) return;
  run(async () => {
    await afterGitHubChange(id, await api("/api/projects/" + encodeURIComponent(id) + "/github", { method: "PUT", body: { clear_token: true } }));
    toast("Token removed");
  });
}

function pushBanner() {
  if (!state.me.push_ready) return null;
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const iOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (iOS && !standalone) {
    return h("div", { class: "banner" }, "To get alerts on iPhone: tap Share, then Add to Home Screen, and open Inrface from there.");
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return null;
  if (Notification.permission === "granted" && pushIsOn) return null;
  if (Notification.permission === "denied") return h("div", { class: "banner" }, "Alerts are blocked for Inrface. You can allow them in this device's settings.");
  return h("div", { class: "banner" }, h("span", null, "Get an alert when something needs you."), h("button", { class: "primary", onclick: enablePush }, "Turn on alerts"));
}

// ---------- actions ----------

function setTab(t) { state.tab = t; location.hash = ""; render(); if (t === "cards") { refresh(); loadDups(); } }

async function run(fn) {
  if (state.busy) return;
  state.busy = true; render();
  try { await fn(); }
  catch (e) { toast(e.message || "That didn't work."); }
  finally { state.busy = false; render(); }
}

function sendChat() {
  const text = state.draft.trim();
  if (!text || state.busy) return;
  state.chat.push({ role: "user", content: text }); state.draft = "";
  run(async () => {
    try {
      const r = await api("/api/chat", { method: "POST", body: { text, project_id: talkProject().id } });
      state.chat.push({ role: "assistant", content: r.reply });
    } catch (e) { state.chat.pop(); state.draft = text; throw e; }
  });
}

function saveDump(asIs) {
  const text = state.draft.trim();
  if (!text) return;
  run(async () => {
    const p = talkProject();
    const r = await api("/api/cards/dump", { method: "POST", body: { text, as_is: asIs, project_id: p.id } });
    state.draft = "";
    const like = (r.looks_like || [])[0];
    toast((r.ids.length === 1 ? "Saved. " : r.ids.length + " ideas saved. ") + receiversOf(p.id) + " will see it."
      + (like ? " It reads a lot like \u201c" + like.like.title + "\u201d, which is already here, so they may merge the two." : ""));
    state.tab = "cards"; await refresh(); loadDups();
  });
}

function saveChat(cardId) {
  run(async () => {
    const r = await api("/api/chat/save", { method: "POST", body: { card_id: cardId, project_id: cardId ? null : talkProject().id } });
    toast(cardId ? "Added to the idea." : r.ids.length === 1 ? "Saved." : r.ids.length + " ideas saved.");
    if (cardId) { state.cardChat = []; await loadCard(cardId); await refresh(); }
    else { state.chat = []; state.tab = "cards"; await refresh(); }
  });
}

function discardChat() {
  if (!window.confirm("Clear this conversation? Nothing has been saved.")) return;
  run(async () => { await api("/api/chat?project_id=" + encodeURIComponent(talkProject().id), { method: "DELETE" }); state.chat = []; });
}

function saveScope(c, k) {
  const get = (f) => state.drafts[k(f)];
  const body = {};
  for (const f of ["title", "summary", "who", "problem", "scope"]) if (get(f) !== undefined) body[f] = get(f);
  for (const f of ["acceptance", "open_questions"]) if (get(f) !== undefined) body[f] = lines(get(f));
  if (!Object.keys(body).length) { toast("Nothing changed"); return; }
  run(async () => {
    state.card = await api("/api/cards/" + c.id + "/scope", { method: "PUT", body });
    for (const key of Object.keys(state.drafts)) if (key.startsWith("scope:" + c.id + ":")) delete state.drafts[key];
    toast("Scope saved");
    await loadCard(c.id); await refresh();
  });
}

// `sure` is for the press that answers a request from an AI app: the banner already said what will happen.
function startBuild(c, only, sure) {
  const where = listNames(only || project(c.project_id)?.build_targets || []);
  const open = only ? [] : stillOpen(c);
  const ask = only ? "Send \"" + c.title + "\" to " + where + " as well?"
    : "Send \"" + c.title + "\" to be built? It goes to " + where + ". The scope is locked once it starts."
      + (open.length ? "\n\n" + c.ready.headline + ":\n" + open.map((x) => "- " + x).join("\n") : "");
  if (!sure && !window.confirm(ask)) return;
  run(async () => {
    try {
      const r = await api("/api/cards/" + c.id + "/build", { method: "POST", body: {} });
      if (r.waiting_for_approval) { toast("Sent for sign-off. " + r.tell_the_user); return; }
      const went = r.sent.filter((x) => x.ok && !x.already).map((x) => x.label);
      const failed = r.sent.filter((x) => !x.ok).map((x) => x.name + " (" + x.error + ")");
      toast((went.length ? "Sent: " + went.join(", ") + "." : "Nothing new was sent.") + (failed.length ? " Didn't reach " + failed.join(", ") + "." : ""));
    } finally {
      await loadCard(c.id); await refresh();
    }
  });
}


// Where sign-offs would be, for editions that don't have them.
function approvalsNote(p) {
  return h("section", { class: "panel", "data-approvals-note": p.id },
    h("h3", null, p.name + ": Sign-offs"),
    h("p", null, "Ask a person or group to sign an idea off before it goes on the roadmap, or before its build starts, in up to three levels."),
    h("label", { class: "check" }, h("input", { type: "checkbox", disabled: true }), h("span", null, "Ask for sign-off before an idea goes on the roadmap")),
    h("label", { class: "check" }, h("input", { type: "checkbox", disabled: true }), h("span", null, "Ask for sign-off before a build starts")),
    h("p", { class: "hint" }, "This is part of the hosted version of Inrface, not this self-hosted edition. See inrface.com to get early hosted access."));
}

function declineBuild(c) {
  run(async () => {
    await api("/api/cards/" + c.id + "/build-ask/decline", { method: "POST", body: {} });
    toast("Left as it was. Nothing was sent.");
    await loadCard(c.id); await refresh();
  });
}


function sendCardChat(id, key) {
  const text = (state.drafts[key] || "").trim();
  if (!text) { toast("Type something first"); return; }
  state.cardChat.push({ role: "user", content: text }); state.drafts[key] = "";
  run(async () => {
    try {
      const r = await api("/api/chat", { method: "POST", body: { text, card_id: id } });
      state.cardChat.push({ role: "assistant", content: r.reply });
    } catch (e) { state.cardChat.pop(); state.drafts[key] = text; throw e; }
  });
}

function postComment(id, kind, key) {
  const text = key ? (state.drafts[key] || "").trim() : "";
  if (kind === "comment" && !text) { toast("Type something first"); return; }
  run(async () => {
    await api("/api/cards/" + id + "/comments", { method: "POST", body: { kind, text } });
    if (key) state.drafts[key] = "";
    toast("Sent");
    await loadCard(id); await refresh();
  });
}

function patch(id, fields, key) {
  const message = key ? (state.drafts[key] || "").trim() : "";
  if (fields.status === "Needs info" && !message) { toast("Type your question first"); const m = document.getElementById("ownerMsg"); if (m) m.focus(); return; }
  run(async () => {
    const r = await api("/api/cards/" + id, { method: "PATCH", body: { ...fields, message: message || null } });
    if (key) state.drafts[key] = "";
    toast(r.waiting_for_approval ? r.tell_the_user : fields.status === "Needs info" ? "Sent back to " + state.card.author_name : fields.status ? "Moved to " + fields.status : "Saved");
    await loadCard(id); await refresh();
  });
}

function draftQuestion(id, key) {
  run(async () => {
    const r = await api("/api/cards/" + id + "/draft-question", { method: "POST", body: {} });
    state.drafts[key] = r.question;
  });
}

function merge(c) {
  const into = document.getElementById("mergeInto").value;
  const target = state.cards.find((x) => x.id === into);
  if (target) mergeCards(c, target);
}

// Fold one idea into another. Both are {id, title}.
function mergeCards(from, into) {
  if (!window.confirm("Merge \u201c" + from.title + "\u201d into \u201c" + into.title + "\u201d?\n\nThe first leaves the list and its comments move across. The second keeps its own write-up. You can split them again later.")) return;
  run(async () => {
    await api("/api/cards/" + from.id + "/merge", { method: "POST", body: { into: into.id } });
    toast("Merged. Both people have been told.");
    state.dups = state.dups.filter((p) => p.fold_in.id !== from.id && p.keep.id !== from.id);
    await refresh(); loadDups();
    if (state.cardId) { state.cardId = null; state.card = null; await openCard(into.id); }
  });
}

function unmergeCard(x) {
  if (!window.confirm("Make \u201c" + x.title + "\u201d its own idea again? It goes back on the list and takes back the comments it brought.")) return;
  run(async () => {
    await api("/api/cards/" + x.id + "/unmerge", { method: "POST", body: {} });
    toast("Split back out");
    await refresh(); loadDups();
    if (state.cardId) await loadCard(state.cardId);
  });
}

function notSame(a, b) {
  run(async () => {
    await api("/api/cards/" + a.id + "/not-same", { method: "POST", body: { other: b.id } });
    state.dups = state.dups.filter((p) => !([p.keep.id, p.fold_in.id].includes(a.id) && [p.keep.id, p.fold_in.id].includes(b.id)));
    if (state.cardId) await loadCard(state.cardId);
    toast("Got it. They won't be suggested together again.");
  });
}

function removeCodeCheck(id) {
  if (!window.confirm("Remove what the code says from this idea?")) return;
  run(async () => { await api("/api/cards/" + id + "/code-check", { method: "DELETE" }); await loadCard(id); });
}

async function copySpec(id) {
  try {
    const item = new ClipboardItem({ "text/plain": api("/api/cards/" + id + "/spec").then((t) => new Blob([t], { type: "text/plain" })) });
    await navigator.clipboard.write([item]);
    toast("Copied");
  } catch (_) {
    try { await navigator.clipboard.writeText(await api("/api/cards/" + id + "/spec")); toast("Copied"); }
    catch (e) { toast("Couldn't copy on this device."); }
  }
}

async function loadCard(id) {
  state.card = await api("/api/cards/" + id);
  const r = await api("/api/chat?card_id=" + encodeURIComponent(id));
  state.cardChat = r.messages;
}

async function openCard(id) {
  state.cardId = id; state.card = null; state.cardChat = []; state.tab = "cards";
  history.replaceState(null, "", "#card=" + encodeURIComponent(id));
  render();
  try {
    await loadCard(id);
    state.home = false;
    // A link or alert can open an idea from another project: show that project's board when the person goes back.
    const pid = state.card && state.card.project_id;
    if (pid && pid !== state.projectId && project(pid)) { state.projectId = pid; try { localStorage.setItem("innerface.project", pid); } catch (_) {} api("/api/chat?project_id=" + encodeURIComponent(pid)).then((r) => { state.chat = r.messages; }, () => {}); }
  }
  catch (e) { toast(e.message); state.cardId = null; history.replaceState(null, "", location.pathname); }
  render();
  $app.querySelector("main")?.scrollTo(0, 0);
}

function closeCard() { state.cardId = null; state.card = null; history.replaceState(null, "", location.pathname); render(); refresh(); loadDups(); }

async function dismissMissed() {
  state.missed = []; render();
  try { await api("/api/seen", { method: "POST", body: {} }); } catch (_) {}
}

let pushIsOn = false;
function b64ToBytes(s) {
  const p = (s + "=".repeat((4 - s.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(p), (ch) => ch.charCodeAt(0));
}
async function savePushSub(sub) {
  const j = sub.toJSON();
  await api("/api/push/subscribe", { method: "POST", body: { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth } });
}
async function enablePush() {
  try {
    if (await Notification.requestPermission() !== "granted") { render(); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(state.me.push_public_key) });
    await savePushSub(sub);
    pushIsOn = true;
    await api("/api/push/test", { method: "POST", body: {} });
    toast("Alerts are on");
  } catch (e) { toast(e.message || "Couldn't turn on alerts."); }
  render();
}

// ---------- start and refresh ----------

async function refresh() {
  try {
    const r = await api("/api/cards");
    const changed = JSON.stringify(r.cards) !== JSON.stringify(state.cards);
    state.cards = r.cards;
    const typing = document.activeElement && ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement.tagName);
    if (changed && !typing && !state.busy) render();
  } catch (e) { if (e.status === 401 || e.status === 0) showExpired(); }
}

function showExpired() {
  if (state.offline) return;
  state.offline = true;
  toast("Your login may have expired. Reopen the app to sign in again.");
}

async function start() {
  try {
    state.me = await api("/api/me");
  } catch (e) {
    $app.replaceChildren(h("main", null, h("p", { class: "empty" }, e.status === 403 ? "This account doesn't have access." : "Couldn't load Inrface."), h("div", { class: "row" }, h("button", { class: "primary", onclick: () => location.reload() }, "Try again"))));
    return;
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then(async (reg) => {
      const sub = await reg.pushManager?.getSubscription();
      if (sub) { pushIsOn = true; savePushSub(sub).catch(() => {}); if (state.tab === "cards") render(); }
    }).catch(() => {});
  }
  let saved = null;
  try { saved = localStorage.getItem("innerface.project"); } catch (_) {}
  try { const v = localStorage.getItem("innerface.view"); if (v === "board" || v === "tools" || v === "usage" || v === "shipped") state.view = v; } catch (_) {}
  if (state.view === "shipped" && !can("changelog")) state.view = "list";
  if (state.view === "shipped") loadShipped();
  if (state.view === "tools") loadTools();
  if (state.view === "usage") loadUsage();
  state.projectId = project(saved) ? saved : state.me.projects[0].id;
  const [cards, chat, missed] = await Promise.all([api("/api/cards"), api("/api/chat?project_id=" + encodeURIComponent(state.projectId)), api("/api/missed")]).catch(() => [{ cards: [] }, { messages: [] }, { items: [] }]);
  state.cards = cards.cards; state.chat = chat.messages; state.missed = missed.items;
  const m = location.hash.match(/^#card=(.+)$/);
  // Open on what needs attention: ideas if something's waiting, otherwise a fresh conversation.
  if (m) { await openCard(decodeURIComponent(m[1])); return; }
  // With several projects the app opens on the list of them.
  state.home = multiProject();
  if (state.missed.length || waitingCount()) state.tab = state.chat.length ? "talk" : "cards";
  render();
  loadDups();
  if (!state.missed.length) api("/api/seen", { method: "POST", body: {} }).catch(() => {});
}

window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#card=(.+)$/);
  if (m && decodeURIComponent(m[1]) !== state.cardId) openCard(decodeURIComponent(m[1]));
});
setInterval(() => { if (document.visibilityState === "visible" && !state.cardId) refresh(); }, 30000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { state.offline = false; refresh(); } });
start();
