/// <reference path="../pb_data/types.d.ts" />
// Server-side rules for the known-issues KB: validation, duplicate refusal, protected fields,
// search index sync, and the /api/kb/* routes. Every client (kb CLI, curl, dashboard) gets the same rules.
// ---- create: normalise, validate, refuse near-duplicates (send header X-KB-Force: 1 to override)
onRecordCreateRequest((e) => {
  const kb = require(`${__hooks}/kb_lib.js`);
  const isAdmin = e.hasSuperuserAuth();
  const hdr = e.requestInfo().headers || {}; // record hooks: header keys are lowercased, "-" becomes "_"
  kb.ensureIndex(e.app);
  kb.normalizeNew(e.record, hdr["x_kb_project"] || "", isAdmin);
  const errs = kb.validate(e.record);
  if (errs.length) throw new BadRequestError("Not saved:\n - " + errs.join("\n - "));
  if (!isAdmin && hdr["x_kb_force"] !== "1") {
    const dups = kb.findDuplicates(e.app, e.record);
    if (dups.length) throw new ApiError(409, "Duplicate: " + JSON.stringify(dups)); // PocketBase drops custom data; the JSON rides in the message
  }
  return e.next();
}, "entries");

// ---- update: agents cannot edit trust fields directly; notes are append-only; fix/cause edits are tracked
onRecordUpdateRequest((e) => {
  const kb = require(`${__hooks}/kb_lib.js`);
  const rec = e.record, orig = rec.original();
  const hdr = e.requestInfo().headers || {};
  if (!e.hasSuperuserAuth()) {
    const locked = kb.changedLocked(rec, orig);
    if (locked.length) throw new BadRequestError(`${locked.join(", ")} cannot be edited directly - use kb confirm / kb obsolete`);
    const was = orig.getString("notes"), now = rec.getString("notes");
    if (now !== was && !now.startsWith(was)) throw new BadRequestError("notes are append-only");
    kb.normalizeTags(rec);
    kb.applyFixChangeRules(rec, orig, hdr["x_kb_project"] || "unknown");
  }
  const errs = kb.validate(rec);
  if (errs.length) throw new BadRequestError("Not saved:\n - " + errs.join("\n - "));
  return e.next();
}, "entries");

// ---- keep the search index in sync (runs for API, dashboard and route writes alike)
onRecordAfterCreateSuccess((e) => { require(`${__hooks}/kb_lib.js`).ftsUpsert(e.app, e.record, true); e.next(); }, "entries");
onRecordAfterUpdateSuccess((e) => { require(`${__hooks}/kb_lib.js`).ftsUpsert(e.app, e.record, false); e.next(); }, "entries");
onRecordAfterDeleteSuccess((e) => { require(`${__hooks}/kb_lib.js`).ftsDelete(e.app, e.record.id); e.next(); }, "entries");

// ---- routes
routerAdd("GET", "/api/kb/search", (e) => {
  const kb = require(`${__hooks}/kb_lib.js`);
  kb.ensureIndex(e.app);
  const q = e.request.url.query();
  const res = kb.search(e.app, {
    q: q.get("q") || "", type: q.get("type") || "", tag: q.get("tag") || "", project: q.get("project") || "",
    all: q.get("all") === "1", limit: parseInt(q.get("limit") || "5", 10) || 5,
  });
  return e.json(200, res);
});

routerAdd("POST", "/api/kb/confirm/{id}", (e) => {
  const kb = require(`${__hooks}/kb_lib.js`);
  const id = e.request.pathValue("id");
  try { e.app.findRecordById("entries", id); } catch (_) { throw new NotFoundError("entry not found"); }
  const rec = kb.confirm(e.app, id, e.requestInfo().body || {}, e.request.header.get("X-KB-Project") || "");
  return e.json(200, kb.out(rec));
});

routerAdd("POST", "/api/kb/obsolete/{id}", (e) => {
  const kb = require(`${__hooks}/kb_lib.js`);
  const id = e.request.pathValue("id");
  try { e.app.findRecordById("entries", id); } catch (_) { throw new NotFoundError("entry not found"); }
  const rec = kb.obsolete(e.app, id, e.requestInfo().body || {}, e.request.header.get("X-KB-Project") || "");
  return e.json(200, kb.out(rec));
});

routerAdd("GET", "/api/kb/info", (e) => {
  const kb = require(`${__hooks}/kb_lib.js`);
  kb.ensureIndex(e.app);
  return e.json(200, { hooks: 1, entries: kb.count(e.app, "entries"), indexed: kb.count(e.app, "entries_fts") });
});
