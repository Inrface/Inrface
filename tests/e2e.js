// Drives the whole loop in a phone-sized browser against a dev server with no AI.
// Usage: node tests/e2e.js <path to playwright> <screenshot folder> [webkit|chromium]
// Server: AUTH_MODE=dev AI_PROVIDER=none INNERFACE_CONFIG=tests/e2e.toml E2E_GH_TOKEN=x GITHUB_API=http://127.0.0.1:8091 GITHUB_POLL_SECONDS=0 WEBHOOK_ALLOW_PRIVATE=1 DATA_DIR=<empty dir> uvicorn innerface.main:app --port 8090
const pw = require(process.argv[2]);
const engine = pw[process.argv[4] || "webkit"];
const SHOTS = process.argv[3]; const URL = "http://127.0.0.1:8090/";
const ZOE = "zoe@example.com", ROBIN = "robin@example.com";
const fails = []; const check = (ok, m) => { console.log((ok ? "ok   " : "FAIL ") + m); if (!ok) fails.push(m); };

// A stand-in for GitHub's create-issue call.
const issues = [];
require("http").createServer((req, res) => {
  let body = ""; req.on("data", (d) => body += d);
  req.on("end", () => {
    if (req.method === "POST" && /^\/repos\/acme\/app\/issues$/.test(req.url)) {
      issues.push({ auth: req.headers.authorization, ...JSON.parse(body) });
      res.writeHead(201, { "content-type": "application/json" });
      return res.end(JSON.stringify({ number: issues.length, html_url: "https://github.com/acme/app/issues/" + issues.length }));
    }
    res.writeHead(404); res.end("{}");
  });
}).listen(8091);

// A stand-in for a system that takes build requests from a webhook connector.
const hooks = [];
require("http").createServer((req, res) => {
  const chunks = []; req.on("data", (d) => chunks.push(d));
  req.on("end", () => {
    const raw = Buffer.concat(chunks);
    hooks.push({ headers: req.headers, raw, body: JSON.parse(raw) });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ url: "https://tracker.example.com/items/7", id: 7 }));
  });
}).listen(8092);
// The check from docs/webhook.md.
function signed(hook, secret) {
  const parts = Object.fromEntries(hook.headers["x-inrface-signature"].split(",").map((p) => p.split("=")));
  const expected = require("crypto").createHmac("sha256", secret).update(parts.t + ".").update(hook.raw).digest("hex");
  return Math.abs(Date.now() / 1000 - Number(parts.t)) < 300 && expected === parts.v1;
}

async function person(browser, who, scheme) {
  const ctx = await browser.newContext({ ...pw.devices["iPhone 13"], colorScheme: scheme || "light", extraHTTPHeaders: { "X-Dev-User": who } });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") { console.log("console:", who, m.text()); fails.push("console " + m.text()); } });
  page.on("pageerror", (e) => { console.log("pageerror:", who, e.message); fails.push("pageerror " + e.message); });
  page.on("dialog", (d) => d.accept());
  return page;
}
const noOverflow = async (p, name) => { check(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no sideways scroll: " + name); check(!/\bnull\b|undefined/.test(await p.evaluate(() => document.body.innerText)), "no stray text: " + name); };
const shot = (p, n) => p.screenshot({ path: `${SHOTS}/${n}.png` });
const openIdeas = async (p) => { await p.goto(URL); await p.waitForSelector("nav"); await p.click('nav button:has-text("Ideas")'); };

(async () => {
  const b = await engine.launch();
  const r = await person(b, ROBIN); const z = await person(b, ZOE);

  await r.goto(URL); await r.waitForSelector("textarea");
  check(await r.locator(".intro").count() === 1, "giver sees the opening prompt");
  check(await r.locator('button:has-text("Talk it through")').count() === 0, "no AI button when AI is off");
  check((await r.textContent(".intro")).includes("Zoe will see it"), "giver is told who sees it");
  await shot(r, "01-new-idea"); await noOverflow(r, "new idea");
  await r.fill("textarea", "Teachers should get a weekly summary of how their classes are doing");
  await r.click("#saveBtn"); await r.waitForSelector(".card");
  check((await r.textContent(".group h2")).includes("Being reviewed"), "giver sees it being reviewed");
  await shot(r, "02-giver-ideas"); await noOverflow(r, "giver ideas");

  await openIdeas(z); await z.waitForSelector(".card");
  check((await z.textContent(".group h2")).includes("Needs you"), "receiver sees it needs them");
  check(await z.locator(".badge").count() === 1, "receiver sees a count on Ideas");
  await z.click(".card"); await z.waitForSelector("#ownerMsg");
  check(await z.locator('button:has-text("Draft a question")').count() === 0, "no AI drafting when AI is off");
  await shot(z, "03-receiver-card"); await noOverflow(z, "receiver card");
  await z.fill("#ownerMsg", "Which numbers matter most to them?");
  await z.click('button:has-text("Send back to Robin")'); await z.waitForSelector(".callout");
  check((await z.textContent(".callout")).includes("You asked"), "receiver sees what was asked");

  await openIdeas(r); await r.waitForSelector(".group.attention");
  check((await r.textContent(".group.attention h2")).includes("Needs your answer"), "giver sees it needs their answer");
  check((await r.textContent(".card .ask")).includes("Which numbers"), "the question shows on the card");
  await r.click(".card"); await r.waitForSelector(".callout textarea");
  check((await r.textContent(".callout h4")).includes("Zoe asks"), "giver sees who asked");
  check(await r.locator('button:has-text("Immediate roadmap")').count() === 0, "giver has no category buttons");
  check(await r.locator('button:has-text("Start build")').count() === 0, "giver has no build button");
  await shot(r, "04-giver-answer"); await noOverflow(r, "giver card");
  await r.fill(".callout textarea", "Attendance and quiz results");
  await r.click('button:has-text("Send answer")'); await r.waitForFunction(() => document.querySelector(".chip")?.textContent === "Answered");

  await openIdeas(z); await z.click(".card"); await z.waitForSelector(".chip");
  check((await z.textContent(".chip")).includes("Answered"), "receiver sees Answered");
  await z.click('summary:has-text("Edit scope")');
  await z.fill('textarea[aria-label="Summary"]', "A weekly email to heads of department with attendance and quiz results.");
  await z.fill('textarea[aria-label="Scope: what\'s in and out"]', "In: weekly email. Out: parent reports.");
  await z.fill('textarea[aria-label="Done when (one per line)"]', "Sends every Monday at 7am\nShows the last 4 weeks");
  await z.fill('textarea[aria-label="Still undecided (one per line)"]', "");
  await z.click('button:has-text("Save scope")'); await z.waitForSelector('.field h4:has-text("Done when")');
  check((await z.textContent(".detail")).includes("Shows the last 4 weeks"), "scope shows on the card");
  await z.setViewportSize({ width: 390, height: 1700 }); await shot(z, "05-scoped"); await z.setViewportSize({ width: 390, height: 664 });
  await noOverflow(z, "scoped card");

  // Add a webhook connector, so the build goes to two places.
  await z.click('.link:has-text("‹ Ideas")'); await z.click('.link:has-text("Settings")');
  await z.waitForSelector('button:has-text("Add connector")');
  await z.selectOption('select[aria-label="Kind of connector"]', "webhook");
  await z.click('button:has-text("Add connector")');
  await z.fill('input[aria-label="Address"]', "http://127.0.0.1:8092/hook");
  await z.setViewportSize({ width: 390, height: 1900 }); await shot(z, "05b-connector-form"); await z.setViewportSize({ width: 390, height: 664 });
  await z.click('.stack-form button.primary:has-text("Add")'); await z.waitForSelector("code.key");
  const secret = await z.textContent("code.key");
  check(/^whsec_.{40,}$/.test(secret), "the signing secret is shown after saving");
  await noOverflow(z, "connector secret");
  await z.click('.callout .link:has-text("Done")');
  check(!(await z.textContent("main")).includes(secret), "the signing secret is not shown again");
  check((await z.textContent("ul.connectors")).includes("not tried yet"), "the new connector is listed");
  await z.click('ul.connectors button:has-text("Test")');
  await z.waitForFunction(() => /last worked/.test(document.querySelector("ul.connectors")?.textContent || ""));
  check(hooks.length === 1 && hooks[0].body.event === "ping" && signed(hooks[0], secret), "Test sends a signed ping");
  await z.setViewportSize({ width: 390, height: 1900 }); await shot(z, "05c-connectors"); await z.setViewportSize({ width: 390, height: 664 });
  await noOverflow(z, "settings");

  // Add a Slack connector before the Slack app exists: Settings shows the address Slack needs.
  await z.selectOption('select[aria-label="Kind of connector"]', "slack");
  await z.click('button:has-text("Add connector")');
  await z.fill('input[aria-label="Ideas channel ID"]', "C0IDEAS0001");
  await z.click('.stack-form button.primary:has-text("Add")');
  await z.waitForFunction(() => /still needs its signing secret and bot token/.test(document.querySelector("ul.connectors")?.textContent || ""));
  check(/^http:\/\/127\.0\.0\.1:8090\/hooks\/slack\/[0-9a-f]{32}$/.test(await z.textContent("ul.connectors .notes code")), "the Slack connector shows its Request URL");
  check(await z.locator('ul.connectors .notes button:has-text("Copy app manifest")').count() === 1, "the Slack app manifest can be copied");
  await z.setViewportSize({ width: 390, height: 1900 }); await shot(z, "05d-slack-connector"); await z.setViewportSize({ width: 390, height: 664 });
  await noOverflow(z, "settings with Slack");
  await z.click('nav button:has-text("Ideas")'); await z.click(".card"); await z.waitForSelector('button:has-text("Start build")');

  await z.click('button:has-text("Start build")'); await z.waitForSelector('a:has-text("Issue #1")');
  check(await z.locator('a[href="https://tracker.example.com/items/7"]:has-text("Sent to Webhook")').count() === 1, "the webhook's link shows on the card");
  check(hooks.length === 2 && hooks[1].body.event === "build.requested" && signed(hooks[1], secret) && !signed(hooks[1], secret + "x"), "the build request is signed");
  check(hooks[1].body.card.acceptance[0] === "Sends every Monday at 7am" && hooks[1].body.requested_by === "Zoe", "the webhook gets the scope");
  check(await z.locator('button:has-text("Send to")').count() === 0, "nothing left to send");
  check(issues.length === 1 && issues[0].labels[0] === "ready-to-build" && issues[0].auth === "Bearer x", "one labeled issue opened with the project token");
  check(issues[0].body.includes("- [ ] Sends every Monday at 7am"), "issue carries the done-when list");
  check((await z.textContent(".chip")).includes("In dev for testing"), "card moves to in dev");
  check(await z.locator('button:has-text("Start build")').count() === 0, "build button goes away");
  await shot(z, "06-building");

  await z.click('.link:has-text("Ideas")'); await z.waitForSelector(".card");
  await z.click('.link:has-text("Giver view")'); await z.waitForSelector('.link:has-text("Your view")');
  await z.click(".card"); await z.waitForSelector(".banner");
  check((await z.textContent(".banner")).includes("Preview only"), "preview has no working actions");
  await z.click('.link:has-text("Ideas")'); await z.click('.link:has-text("Your view")');

  await openIdeas(r); await r.waitForSelector(".card");
  check((await r.textContent(".group h2")).includes("In dev for testing"), "giver sees it being built");
  await r.click(".card"); await r.waitForSelector('a:has-text("Issue #1")');
  await r.click('button:has-text("I don\'t like this")'); await r.waitForSelector(".comment:has-text(\"I don't like this\")");
  await r.click('.link:has-text("Ideas")'); await r.click('nav button:has-text("New idea")');
  await r.fill("textarea", "Dark mode for parents");
  await r.click("#saveBtn"); await r.waitForFunction(() => document.querySelectorAll(".card").length === 2);
  check(true, "a second idea saved");

  await z.evaluate(() => fetch("/api/seen", { method: "POST", headers: { "X-Requested-With": "Inrface" } }));
  await r.waitForTimeout(50);
  await r.click('.card:has-text("Dark mode")'); await r.waitForSelector('textarea[aria-label="Add a comment"]');
  await r.fill('textarea[aria-label="Add a comment"]', "One more thought"); await r.click('button:has-text("Comment")');
  await r.waitForSelector('.comment:has-text("One more thought")');
  await openIdeas(z); await z.waitForSelector(".missed");
  await shot(z, "07-receiver-missed"); check(true, "receiver sees While you were away");
  await z.click('.missed button:has-text("Got it")');
  await z.click('.card:has-text("Dark mode")'); await z.waitForSelector("#ownerMsg");
  await z.click('button:has-text("Far down the line")'); await z.waitForFunction(() => document.querySelector(".chip")?.textContent === "Far down the line");
  check(true, "receiver files an idea on the roadmap");

  const dark = await person(b, ROBIN, "dark"); await openIdeas(dark); await dark.waitForSelector(".card"); await shot(dark, "08-dark");
  await noOverflow(dark, "dark");
  await b.close();
  console.log(fails.length ? "FAILED: " + fails.length : "ALL PASSED"); process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error("CRASH", e.message); process.exit(2); });
