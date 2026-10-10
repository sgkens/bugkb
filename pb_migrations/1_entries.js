/// <reference path="../pb_data/types.d.ts" />
// Creates the single `entries` collection that holds bugs, perf findings and gotchas.
// Runs automatically the first time `pocketbase serve` starts.
migrate((app) => {
  const collection = new Collection({
    type: "base",
    name: "entries",

    // Local-only tool: the server binds to 127.0.0.1 by default, so reads/writes are open.
    // Deleting is superuser-only on purpose -- agents mark entries `obsolete` instead.
    listRule: "",
    viewRule: "",
    createRule: "",
    updateRule: "",
    deleteRule: null,

    fields: [
      { type: "select", name: "type", required: true, maxSelect: 1,
        values: ["bug", "perf", "gotcha"] },
      { type: "text", name: "title", required: true, max: 300 },
      { type: "text", name: "symptom", max: 20000 },   // what you SEE: exact error text, behaviour
      { type: "text", name: "cause", max: 20000 },     // why it happens
      { type: "text", name: "fix", max: 20000 },       // what resolved it / what to do
      { type: "text", name: "project", max: 200 },     // project it was first found in
      { type: "json", name: "tags", maxSize: 20000 },  // technologies: ["powershell","gitlab-ci",...]
      { type: "select", name: "scope", maxSelect: 1,
        values: ["general", "project"] },              // general = applies beyond the origin project
      { type: "select", name: "status", required: true, maxSelect: 1,
        values: ["unverified", "confirmed", "obsolete"] },
      { type: "number", name: "hits", min: 0 },        // times this was encountered / re-confirmed
      { type: "json", name: "seen_in", maxSize: 20000 },  // projects that hit it
      { type: "json", name: "env", maxSize: 20000 },      // versions / OS the fix was verified against
      { type: "json", name: "metrics", maxSize: 20000 },  // perf: {metric, before, after, unit, conditions}
      { type: "json", name: "related", maxSize: 20000 },  // ids of related entries
      { type: "text", name: "supersedes", max: 50 },      // id of the entry this replaces
      { type: "text", name: "source", max: 200 },         // who wrote it: agent / model / human
      { type: "text", name: "notes", max: 20000 },        // append-only re-confirmation notes
      { type: "autodate", name: "created", onCreate: true },
      { type: "autodate", name: "updated", onCreate: true, onUpdate: true },
    ],

    indexes: [
      "CREATE INDEX idx_entries_status ON entries (status)",
      "CREATE INDEX idx_entries_type ON entries (type)",
      "CREATE INDEX idx_entries_project ON entries (project)",
    ],
  });

  app.save(collection);
}, (app) => {
  app.delete(app.findCollectionByNameOrId("entries"));
});
