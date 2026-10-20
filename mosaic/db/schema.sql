-- Schema di Mosaic 2 (migrazione 1). SQLite, WAL, chiavi esterne attive.

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE folders (
    id   INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    path TEXT NOT NULL,
    UNIQUE (root, path)
);

CREATE TABLE cameras (
    id    INTEGER PRIMARY KEY,
    make  TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    UNIQUE (make, model)
);

CREATE TABLE lenses (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE photos (
    id             INTEGER PRIMARY KEY,
    uid            TEXT NOT NULL UNIQUE,
    uuid           TEXT NOT NULL DEFAULT '',
    type           TEXT NOT NULL DEFAULT 'image',      -- image | raw | video | live | animated
    title          TEXT NOT NULL DEFAULT '',
    title_src      INTEGER NOT NULL DEFAULT 0,
    caption        TEXT NOT NULL DEFAULT '',
    caption_src    INTEGER NOT NULL DEFAULT 0,
    taken_at       TEXT,
    taken_at_local TEXT,
    taken_src      INTEGER NOT NULL DEFAULT 0,
    time_zone      TEXT NOT NULL DEFAULT '',
    lat            REAL,
    lng            REAL,
    altitude       REAL,
    camera_id      INTEGER REFERENCES cameras(id),
    lens_id        INTEGER REFERENCES lenses(id),
    camera_serial  TEXT NOT NULL DEFAULT '',
    iso            INTEGER,
    exposure       TEXT NOT NULL DEFAULT '',
    f_number       REAL,
    focal_length   INTEGER,
    width          INTEGER NOT NULL DEFAULT 0,
    height         INTEGER NOT NULL DEFAULT 0,
    duration       REAL NOT NULL DEFAULT 0,
    favorite       INTEGER NOT NULL DEFAULT 0,
    private        INTEGER NOT NULL DEFAULT 0,
    nsfw           INTEGER NOT NULL DEFAULT 0,
    rating         INTEGER NOT NULL DEFAULT 0,
    quality        INTEGER NOT NULL DEFAULT 0,
    stack_key      TEXT NOT NULL DEFAULT '',
    stack_key_seq  TEXT NOT NULL DEFAULT '',
    seq            INTEGER,
    folder_id      INTEGER REFERENCES folders(id),
    main_color     TEXT NOT NULL DEFAULT '',
    colors         TEXT NOT NULL DEFAULT '',
    luminance      REAL,
    chroma         REAL,
    phash          INTEGER,
    meta_at        TEXT,
    analyzed_at    TEXT,
    faces_at       TEXT,
    captioned_at   TEXT,
    deleted_at     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX idx_photos_taken     ON photos(taken_at);
CREATE INDEX idx_photos_deleted   ON photos(deleted_at);
CREATE INDEX idx_photos_folder    ON photos(folder_id);
CREATE INDEX idx_photos_stack     ON photos(stack_key);
CREATE INDEX idx_photos_stack_seq ON photos(stack_key_seq);
CREATE INDEX idx_photos_uuid      ON photos(uuid);
CREATE INDEX idx_photos_type      ON photos(type);

CREATE TABLE files (
    id            INTEGER PRIMARY KEY,
    photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    root          TEXT NOT NULL,
    dir           TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    original_name TEXT NOT NULL,
    sha1          TEXT NOT NULL DEFAULT '',
    sha256        TEXT NOT NULL DEFAULT '',
    size          INTEGER NOT NULL DEFAULT 0,
    mod_time      REAL NOT NULL DEFAULT 0,
    mime          TEXT NOT NULL DEFAULT '',
    media_type    TEXT NOT NULL,                 -- image | raw | heic | video | sidecar
    is_primary    INTEGER NOT NULL DEFAULT 0,
    missing       INTEGER NOT NULL DEFAULT 0,
    codec         TEXT NOT NULL DEFAULT '',
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    orientation   INTEGER NOT NULL DEFAULT 1,
    duration      REAL NOT NULL DEFAULT 0,
    fps           REAL NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    UNIQUE (root, dir, name)
);
CREATE INDEX idx_files_photo  ON files(photo_id);
CREATE INDEX idx_files_sha1   ON files(sha1);
CREATE INDEX idx_files_sha256 ON files(sha256);

CREATE TABLE duplicates (
    id       INTEGER PRIMARY KEY,
    root     TEXT NOT NULL,
    dir      TEXT NOT NULL DEFAULT '',
    name     TEXT NOT NULL,
    sha1     TEXT NOT NULL,
    size     INTEGER NOT NULL DEFAULT 0,
    mod_time REAL NOT NULL DEFAULT 0,
    of_file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE (root, dir, name)
);

CREATE TABLE errors (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    scope   TEXT NOT NULL,
    ref     TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL
);

CREATE TABLE jobs (
    id          INTEGER PRIMARY KEY,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | cancelled
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    progress    REAL NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX idx_jobs_state ON jobs(state, id);

CREATE TABLE labels (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    slug     TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE photos_labels (
    photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    label_id    INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    uncertainty INTEGER NOT NULL DEFAULT 100,    -- 100 - confidenza %
    src         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (photo_id, label_id)
);
CREATE INDEX idx_photos_labels_label ON photos_labels(label_id);

CREATE TABLE keywords (
    id   INTEGER PRIMARY KEY,
    word TEXT NOT NULL UNIQUE
);

CREATE TABLE photos_keywords (
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    src        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (photo_id, keyword_id)
);

CREATE TABLE embeddings (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL,
    PRIMARY KEY (photo_id, model_id)
);

CREATE TABLE subjects (
    id           INTEGER PRIMARY KEY,
    uid          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL UNIQUE,
    hidden       INTEGER NOT NULL DEFAULT 0,
    favorite     INTEGER NOT NULL DEFAULT 0,
    marker_count INTEGER NOT NULL DEFAULT 0,
    photo_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE faces (
    id               TEXT PRIMARY KEY,           -- hash del centroide
    subj_id          INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    kind             INTEGER NOT NULL DEFAULT 0, -- 0 automatico, 1 manuale
    hidden           INTEGER NOT NULL DEFAULT 0,
    samples          INTEGER NOT NULL DEFAULT 0,
    sample_radius    REAL NOT NULL DEFAULT 0,
    collisions       INTEGER NOT NULL DEFAULT 0,
    collision_radius REAL NOT NULL DEFAULT 0,
    embed_model      TEXT NOT NULL DEFAULT '',
    embedding        BLOB NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE markers (
    id           INTEGER PRIMARY KEY,
    uid          TEXT NOT NULL UNIQUE,
    file_id      INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    photo_id     INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    type         TEXT NOT NULL DEFAULT 'face',
    src          INTEGER NOT NULL DEFAULT 12,
    name         TEXT NOT NULL DEFAULT '',
    subj_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    face_id      TEXT REFERENCES faces(id) ON DELETE SET NULL,
    face_dist    REAL NOT NULL DEFAULT -1,
    embedding    BLOB,
    embed_model  TEXT NOT NULL DEFAULT '',
    detect_model TEXT NOT NULL DEFAULT '',
    landmarks    TEXT NOT NULL DEFAULT '',
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
    size         INTEGER NOT NULL DEFAULT 0,
    score        INTEGER NOT NULL DEFAULT 0,
    invalid      INTEGER NOT NULL DEFAULT 0,
    matched_at   TEXT
);
CREATE INDEX idx_markers_photo ON markers(photo_id);
CREATE INDEX idx_markers_face  ON markers(face_id);
CREATE INDEX idx_markers_subj  ON markers(subj_id);

CREATE TABLE albums (
    id             INTEGER PRIMARY KEY,
    uid            TEXT NOT NULL UNIQUE,
    type           TEXT NOT NULL,                -- album | folder | month | moment
    slug           TEXT NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    filter         TEXT NOT NULL DEFAULT '',     -- query serializzata per gli smart album
    favorite       INTEGER NOT NULL DEFAULT 0,
    private        INTEGER NOT NULL DEFAULT 0,
    cover_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    photo_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT,
    UNIQUE (type, slug)
);

CREATE TABLE photos_albums (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    PRIMARY KEY (album_id, photo_id)
);

CREATE TABLE rename_journal (
    id       INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL,
    root     TEXT NOT NULL,
    old_dir  TEXT NOT NULL,
    old_name TEXT NOT NULL,
    new_dir  TEXT NOT NULL,
    new_name TEXT NOT NULL,
    state    TEXT NOT NULL DEFAULT 'planned',    -- planned | done | failed | rolled_back
    error    TEXT NOT NULL DEFAULT '',
    ts       TEXT NOT NULL
);
CREATE INDEX idx_journal_batch ON rename_journal(batch_id, id);

CREATE VIRTUAL TABLE photos_fts USING fts5(
    title, caption, keywords, name, path, labels,
    tokenize = 'unicode61 remove_diacritics 2'
);
