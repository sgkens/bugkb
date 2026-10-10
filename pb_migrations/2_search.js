/// <reference path="../pb_data/types.d.ts" />
// Search index (FTS5), a sort index for "recent", and a review-queue view.
migrate((app) => {
  const entries = app.findCollectionByNameOrId("entries");
  entries.addIndex("idx_entries_updated", false, "updated", "");
  app.save(entries);

  // Standalone FTS5 table (not external-content: PocketBase tables have no stable integer rowid).
  // `id` is an INDEXED column on purpose: it makes delete-by-id an index lookup (an UNINDEXED id would
  // force a full scan of the index on every update). Searches use a column filter so ids never match text.
  // Kept in sync by pb_hooks/kb.pb.js; rebuilt automatically at first use if the row counts differ.
  // porter = stemming (mocking/mocked/mock), unicode61 = case/diacritic folding.
  app.db().newQuery(
    "CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(" +
    "id, title, symptom, cause, fix, tags, tokenize='porter unicode61')"
  ).execute();

  // Read-only view: what a human should look at next (most-hit unverified entries first via ?sort=-hits).
  app.save(new Collection({
    type: "view",
    name: "review_queue",
    listRule: "",
    viewRule: "",
    viewQuery: "SELECT id, title, type, project, hits, created FROM entries WHERE status = 'unverified'",
  }));
}, (app) => {
  try { app.delete(app.findCollectionByNameOrId("review_queue")); } catch (_) {}
  app.db().newQuery("DROP TABLE IF EXISTS entries_fts").execute();
  const entries = app.findCollectionByNameOrId("entries");
  entries.removeIndex("idx_entries_updated");
  app.save(entries);
});
