// Shared logic for the known-issues KB. Loaded with require(`${__hooks}/kb_lib.js`) INSIDE each handler
// (PocketBase runs handlers in isolated JS contexts, so top-level variables are not shared).

const STOP = new Set(("the and for with that this from have has had was were are not but you your our can " +
  "could should would will does did into out over after before when while how why what which then than " +
  "them they there their its just also only very more some any all").split(" "));

const SECRET_PATTERNS = [
  /glpat-[A-Za-z0-9_\-]{10,}/, /ghp_[A-Za-z0-9]{20,}/, /github_pat_[A-Za-z0-9_]{20,}/, /AKIA[0-9A-Z]{16}/,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/, /xox[baprs]-[A-Za-z0-9\-]{10,}/, /sk-[A-Za-z0-9]{20,}/,
  /(password|passwd|secret|token|api[_-]?key)['"]?\s*[=:]\s*['"]?[^\s'"]{8,}/i,
];

const LOCKED_FIELDS = ["status", "hits", "seen_in", "project", "source"];
const NOTES_MAX = 15000; // entries.notes is capped at 20000 by the schema; keep headroom
const FTS_COLS = "id, title, symptom, cause, fix, tags";

// Words that dominate pasted logs/tracebacks but never identify a problem. Dropped from QUERIES only.
const NOISE = new Set(("traceback most recent call last file line home usr lib bin src opt var tmp char column stack frame").split(" "));

// Word characters: ASCII plus explicit non-ASCII letter ranges (Latin-1+, Cyrillic, Greek, Arabic, CJK, ...).
// Not \p{L}: the embedded JS engine parses it without honouring it. Skips U+2000-U+2BFF (punctuation, symbols,
// arrows) and U+3000-U+303F (CJK punctuation), so smart quotes do not glue onto words.
const TOKEN_RE = /[A-Za-z0-9_.\-\u00C0-\u1FFF\u2C00-\u2FFF\u3040-\uD7FF]+/g;

// ------------------------------------------------------------------ text helpers
function tokens(text) {
  const out = [];
  const m = String(text || "").toLowerCase().match(TOKEN_RE) || [];
  for (let t of m) {
    t = t.replace(/^[._\-]+|[._\-]+$/g, "");
    if (t.length >= 3 && !STOP.has(t)) out.push(t);
  }
  return out;
}
function uniq(a) { return Array.from(new Set(a)); }

// Pick the terms that best identify a problem from free text (a pasted traceback, an error line, keywords).
// Taking the first N terms loses the error class at the END of a traceback, so rank by distinctiveness instead:
// long, dotted / snake_case / camelCase / mixed letter-digit tokens score high; log boilerplate is dropped.
function queryTerms(text, max) {
  const best = new Map(), src = String(text || "").match(TOKEN_RE) || [];
  src.forEach((raw, pos) => {
    const t = raw.toLowerCase().replace(/^[._\-]+|[._\-]+$/g, "");
    if (t.length < 3 || STOP.has(t) || NOISE.has(t) || /^[0-9._\-]+$/.test(t)) return;
    let score = Math.min(t.length, 24);
    if (/[a-z0-9][._][a-z0-9]/i.test(raw)) score += 6;      // dotted or snake_case identifier
    if (/[a-z][A-Z]/.test(raw)) score += 6;                  // camelCase / PascalCase (exception class names)
    if (/[a-z]/i.test(raw) && /[0-9]/.test(raw)) score += 3; // sqlite3, ipv6, http2
    const prev = best.get(t);
    if (!prev || score > prev.score) best.set(t, { t, score, pos: prev ? prev.pos : pos });
  });
  return Array.from(best.values()).sort((a, b) => (b.score - a.score) || (a.pos - b.pos)).slice(0, max).map((x) => x.t);
}
// crude, symmetric stemmer used only for the coverage/duplicate heuristics (FTS does the real stemming)
function stem(w) {
  if (w.length > 5) w = w.replace(/(ing|ed|es|s|ly)$/, "");
  if (w.length > 4) w = w.replace(/e$/, "");
  return w;
}
function stemSet(text) {
  const s = new Set();
  for (const t of tokens(text)) {
    s.add(stem(t));
    for (const p of t.split(/[._\-]+/)) if (p.length >= 3 && !STOP.has(p)) s.add(stem(p));
  }
  return s;
}
function clip(s, n) { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length <= n ? s : s.slice(0, n - 1) + "…"; }
function today() { return new Date().toISOString().slice(0, 10); }

// ------------------------------------------------------------------ record helpers
function jsonGet(rec, key, fallback) {
  try {
    const s = rec.getString(key);
    if (!s || s === "null") return fallback;
    return JSON.parse(s);
  } catch (_) { return fallback; }
}
function tagsOf(rec) { const t = jsonGet(rec, "tags", []); return Array.isArray(t) ? t : []; }
function seenIn(rec) { const t = jsonGet(rec, "seen_in", []); return Array.isArray(t) ? t : []; }
function blob(rec) {
  return ["title", "symptom", "cause", "fix"].map((k) => rec.getString(k)).join(" ") + " " + tagsOf(rec).join(" ");
}
function out(rec) { return rec.publicExport(); }

function appendNote(rec, line) {
  let notes = (rec.getString("notes") + "\n" + line).trim();
  if (notes.length > NOTES_MAX) {
    const lines = notes.split("\n");
    while (notes.length > NOTES_MAX && lines.length > 1) { lines.shift(); notes = lines.join("\n"); }
    if (notes.length > NOTES_MAX) notes = notes.slice(notes.length - NOTES_MAX);
  }
  rec.set("notes", notes);
}

// ------------------------------------------------------------------ validation / normalisation
function validate(rec) {
  const errs = [];
  const type = rec.getString("type");
  const symptom = rec.getString("symptom").trim();
  const fix = rec.getString("fix").trim();
  if (rec.getString("title").trim().length < 8) errs.push("title is required (a specific one-line summary, 8+ chars)");
  if (!rec.getString("project").trim()) errs.push("project is required (send it, or the X-KB-Project header)");
  if (!tagsOf(rec).length) errs.push("at least one tag is required - tag the TECHNOLOGY (e.g. powershell, docker, gitlab-ci) so other projects can find it");
  if ((type === "bug" || type === "gotcha") && (!symptom || !fix)) errs.push(`a ${type} needs both symptom (what you see; include exact error text) and fix`);
  if (type === "perf") {
    const m = jsonGet(rec, "metrics", {});
    if (!m || typeof m.before !== "number" || !Number.isFinite(m.before) || typeof m.after !== "number" || !Number.isFinite(m.after))
      errs.push('a perf entry needs metrics with at least before and after, e.g. {"metric":"list-render","before":1200,"after":90,"unit":"ms","conditions":"10k rows"}');
    if (!fix) errs.push("a perf entry needs fix (what change produced the improvement)");
  }
  const text = ["title", "symptom", "cause", "fix", "notes", "env", "metrics", "project", "source", "tags", "related", "supersedes"].map((k) => rec.getString(k)).join("\n");
  if (SECRET_PATTERNS.some((p) => p.test(text)))
    errs.push("the text looks like it contains a credential or secret - redact it (use placeholders like <TOKEN>)");
  return errs;
}

function normalizeTags(rec) {
  rec.set("tags", uniq(tagsOf(rec).map((t) => String(t).trim().toLowerCase()).filter(Boolean)));
}

function normalizeNew(rec, actor, isAdmin) {
  const project = (rec.getString("project") || actor || "").trim();
  rec.set("project", project);
  normalizeTags(rec);
  if (!rec.getString("scope")) rec.set("scope", "general");
  if (!rec.getString("source")) rec.set("source", isAdmin ? "human" : "agent");
  if (!isAdmin) { // clients cannot self-award trust
    rec.set("source", "agent");
    rec.set("status", "unverified");
    rec.set("hits", 1);
    rec.set("seen_in", project ? [project] : []);
    rec.set("notes", "");
  } else {
    if (!rec.getString("status")) rec.set("status", "unverified");
    if (!rec.getInt("hits")) rec.set("hits", 1);
    if (!seenIn(rec).length && project) rec.set("seen_in", [project]);
  }
}

function changedLocked(rec, orig) {
  const bad = [];
  for (const f of LOCKED_FIELDS) {
    const a = ["seen_in"].includes(f) ? JSON.stringify(seenIn(orig)) : String(orig.get(f));
    const b = ["seen_in"].includes(f) ? JSON.stringify(seenIn(rec)) : String(rec.get(f));
    if (a !== b) bad.push(f);
  }
  return bad;
}

// Editing the fix/cause keeps the old text in notes and drops a confirmed entry back to unverified.
function applyFixChangeRules(rec, orig, actor) {
  for (const k of ["fix", "cause"]) {
    const was = orig.getString(k);
    if (was && rec.getString(k) !== was) appendNote(rec, `[${today()}] ${actor}: ${k} changed. Previous ${k}: ${clip(was, 600)}`);
  }
  const changed = ["fix", "cause"].some((k) => orig.getString(k) && rec.getString(k) !== orig.getString(k));
  if (changed && rec.getString("status") === "confirmed") {
    rec.set("status", "unverified");
    appendNote(rec, `[${today()}] status confirmed -> unverified (fix/cause changed; needs re-confirmation)`);
  }
}

// ------------------------------------------------------------------ FTS index
// delete+insert must be atomic: interleaved upserts of one id otherwise leave duplicate index rows.
// runInTransaction reuses an already-open transaction, and PocketBase opens write transactions IMMEDIATE,
// so concurrent writers queue instead of interleaving.
function ftsUpsert(app, rec, isNew) {
  app.runInTransaction((tx) => {
    if (!isNew) ftsDelete(tx, rec.id);
    tx.db().newQuery(`INSERT INTO entries_fts(${FTS_COLS}) VALUES ({:id},{:title},{:symptom},{:cause},{:fix},{:tags})`).bind({
      id: rec.id, title: rec.getString("title"), symptom: rec.getString("symptom"),
      cause: rec.getString("cause"), fix: rec.getString("fix"), tags: tagsOf(rec).join(" "),
    }).execute();
  });
}
function ftsDelete(app, id) {
  // MATCH on the indexed id column = index lookup, not a table scan
  app.db().newQuery("DELETE FROM entries_fts WHERE entries_fts MATCH {:m}").bind({ m: 'id : "' + id + '"' }).execute();
}
function count(app, table) {
  // FTS5 keeps one row per document in its shadow table *_docsize: much cheaper to count than the index itself
  const t = table === "entries_fts" ? "entries_fts_docsize" : table;
  const row = new DynamicModel({ n: 0 });
  app.db().newQuery(`SELECT COUNT(*) AS n FROM ${t}`).one(row);
  return row.n;
}
function reindex(app) {
  app.runInTransaction((tx) => {
    tx.db().newQuery("DELETE FROM entries_fts").execute();
    tx.db().newQuery(`INSERT INTO entries_fts(${FTS_COLS}) SELECT id, title, symptom, cause, fix, tags FROM entries`).execute();
  });
}
function reindexIfNeeded(app) {
  const a = count(app, "entries"), b = count(app, "entries_fts");
  if (a !== b) { const t0 = Date.now(); reindex(app); console.log(`[kb] search index rebuilt: ${a} entries (${Date.now() - t0} ms)`); }
}
// Checked on first use and then at most once a minute (there is no reliable "after migrations" startup hook in the
// JS runtime). The check is two COUNTs, so it is cheap; `bugkb --start` pings /api/kb/info so it also runs at startup.
const INDEX_CHECK_MS = 60000;
function ensureIndex(app) {
  const last = app.store().get("kb_index_checked");
  if (last && Date.now() - last < INDEX_CHECK_MS) return;
  reindexIfNeeded(app);
  app.store().set("kb_index_checked", Date.now());
}

function ftsCandidates(app, text, limit) {
  const terms = queryTerms(text, 12);
  if (!terms.length) return { terms, hits: [] };
  const q = "{title symptom cause fix tags} : (" + terms.map((t) => '"' + t.replace(/"/g, '""') + '"').join(" OR ") + ")";
  const rows = arrayOf(new DynamicModel({ id: "", score: 0.5 }));
  app.db().newQuery(
    "SELECT id, bm25(entries_fts, 0, 6, 4, 1, 2, 3) AS score FROM entries_fts WHERE entries_fts MATCH {:q} ORDER BY score LIMIT {:n}"
  ).bind({ q, n: limit }).all(rows);
  return { terms, hits: rows.map((r) => ({ id: r.id, score: r.score })) };
}

function coverage(rec, terms) {
  const set = stemSet(blob(rec));
  return terms.filter((t) => set.has(stem(t))).length;
}

function search(app, p) {
  const cand = ftsCandidates(app, p.q, 40);
  if (!cand.hits.length) return { terms: cand.terms, items: [] };
  const recs = app.findRecordsByIds("entries", cand.hits.map((h) => h.id));
  const byId = {};
  recs.forEach((r) => { byId[r.id] = r; });
  // 40% of the terms, capped at 3: a long pasted traceback carries many terms that no entry will repeat.
  const need = Math.max(1, Math.min(3, Math.ceil(cand.terms.length * 0.4)));
  const wantTags = p.tag ? String(p.tag).toLowerCase().split(/[,\s]+/).filter(Boolean) : null;
  const items = [], seenIds = new Set();
  for (const h of cand.hits) {
    const r = byId[h.id];
    if (!r || seenIds.has(h.id)) continue; // an id can appear twice if the index ever drifted
    seenIds.add(h.id);
    if (!p.all && r.getString("status") === "obsolete") continue;
    if (p.type && r.getString("type") !== p.type) continue;
    if (p.project && r.getString("project") !== p.project && !seenIn(r).includes(p.project)) continue;
    if (wantTags && !tagsOf(r).some((t) => wantTags.includes(t))) continue;
    const cov = coverage(r, cand.terms);
    if (cov < need) continue;
    const boost = (r.getString("status") === "confirmed" ? 1.25 : 1) * (1 + Math.min(r.getInt("hits"), 5) * 0.02);
    items.push({ r, cov, rank: -h.score * boost });
  }
  items.sort((a, b) => (b.rank - a.rank) || (b.cov - a.cov));
  return { terms: cand.terms, items: items.slice(0, p.limit || 5).map((x) => Object.assign(out(x.r), { _score: x.rank, _coverage: x.cov })) };
}

function findDuplicates(app, rec) {
  const text = rec.getString("title") + " " + rec.getString("symptom") + " " + tagsOf(rec).join(" ");
  const cand = ftsCandidates(app, text, 30);
  if (!cand.hits.length) return [];
  const mine = stemSet(rec.getString("title") + " " + rec.getString("symptom"));
  const title = rec.getString("title").trim().toLowerCase();
  const dups = [];
  for (const r of app.findRecordsByIds("entries", cand.hits.map((h) => h.id))) {
    if (r.getString("status") === "obsolete") continue;
    const theirs = stemSet(r.getString("title") + " " + r.getString("symptom"));
    let inter = 0;
    mine.forEach((t) => { if (theirs.has(t)) inter++; });
    const sim = mine.size && theirs.size ? inter / Math.min(mine.size, theirs.size) : 0;
    const same = r.getString("title").trim().toLowerCase() === title;
    if (same || (sim >= 0.55 && inter >= 3)) dups.push({ id: r.id, title: r.getString("title"), similarity: Math.round(sim * 100) / 100 });
  }
  return dups.sort((a, b) => b.similarity - a.similarity).slice(0, 3);
}

// ------------------------------------------------------------------ state transitions (server is the authority)
// A confirmed fix that worked on a different version must not erase the versions it was already verified on.
function mergeEnv(oldEnv, add) {
  const out = Object.assign({}, oldEnv);
  for (const k of Object.keys(add)) {
    const nv = typeof add[k] === "string" ? add[k] : JSON.stringify(add[k]);
    const cur = out[k] === undefined ? [] : String(out[k]).split(/\s*,\s*/);
    if (!cur.includes(nv)) cur.push(nv);
    out[k] = cur.join(", ");
  }
  return out;
}

// confirm/obsolete are read-modify-write: run them in one transaction so parallel agents cannot lose updates.
function confirm(app, id, body, actor) {
  let result;
  app.runInTransaction((tx) => { result = confirmTx(tx, id, body, actor); });
  return result;
}
function confirmTx(app, id, body, actor) {
  const rec = app.findRecordById("entries", id);
  const project = String(body.project || actor || "unknown");
  const seen = seenIn(rec);
  if (!seen.includes(project)) seen.push(project);
  const hits = rec.getInt("hits") + 1;
  let status = rec.getString("status");
  if (status === "unverified" && (seen.length >= 2 || hits >= 3)) status = "confirmed";
  if (status === "obsolete") status = "unverified"; // it bit someone again: needs a human look
  const note = body.note ? String(body.note) : "";
  if (note && SECRET_PATTERNS.some((p) => p.test(note))) throw new BadRequestError("the note looks like it contains a credential or secret");
  rec.set("hits", hits);
  rec.set("seen_in", seen);
  rec.set("status", status);
  if (body.env !== undefined && (!body.env || typeof body.env !== "object" || Array.isArray(body.env)))
    throw new BadRequestError("env must be a JSON object");
  if (body.env) rec.set("env", mergeEnv(jsonGet(rec, "env", {}), body.env));
  appendNote(rec, `[${today()}] ${project}: confirmed` + (note ? ` - ${note}` : ""));
  const errs = validate(rec);
  if (errs.length) throw new BadRequestError("Not saved: " + errs.join("; "));
  app.save(rec);
  return rec;
}

function obsolete(app, id, body, actor) {
  let result;
  app.runInTransaction((tx) => { result = obsoleteTx(tx, id, body, actor); });
  return result;
}
function obsoleteTx(app, id, body, actor) {
  if (!body.reason) throw new BadRequestError("reason is required");
  const rec = app.findRecordById("entries", id);
  const reason = String(body.reason);
  if (SECRET_PATTERNS.some((p) => p.test(reason))) throw new BadRequestError("the reason looks like it contains a credential or secret");
  rec.set("status", "obsolete");
  appendNote(rec, `[${today()}] ${body.project || actor || "unknown"}: marked obsolete - ${reason}` +
    (body.superseded_by ? ` (superseded by #${body.superseded_by})` : ""));
  const errs = validate(rec);
  if (errs.length) throw new BadRequestError("Not saved: " + errs.join("; "));
  app.save(rec);
  if (body.superseded_by) {
    const newer = app.findRecordById("entries", String(body.superseded_by));
    newer.set("supersedes", id);
    app.save(newer);
  }
  return rec;
}

module.exports = {
  tokens, queryTerms, out, jsonGet, validate, normalizeNew, normalizeTags, changedLocked, applyFixChangeRules,
  ftsUpsert, ftsDelete, reindex, reindexIfNeeded, ensureIndex, count, search, findDuplicates, confirm, obsolete,
};
