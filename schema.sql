-- Local copy of the store's public catalogue, plus data-quality findings and a change log.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,      -- Shopify product id
    handle       TEXT NOT NULL,
    title        TEXT NOT NULL,
    product_type TEXT,
    vendor       TEXT,
    tags         TEXT,                      -- comma separated
    description  TEXT,                      -- plain text from body_html
    options      TEXT,                      -- JSON: [{name, values}]
    published_at TEXT,
    updated_at   TEXT,
    synced_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variants (
    id          INTEGER PRIMARY KEY,        -- Shopify variant id
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    title       TEXT,
    option1     TEXT, option2 TEXT, option3 TEXT,
    sku         TEXT,
    price       REAL,
    compare_at  REAL,
    available   INTEGER,
    position    INTEGER
);

CREATE TABLE IF NOT EXISTS images (
    id          INTEGER NOT NULL,           -- Shopify image id (the same image can be used by several products)
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    src         TEXT,
    alt         TEXT,
    position    INTEGER,
    variant_ids TEXT,                        -- JSON list
    PRIMARY KEY (product_id, id)
);

-- Data-quality findings with a suggested fix, rebuilt on every check
CREATE TABLE IF NOT EXISTS issues (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL,              -- High / Medium / Low
    target      TEXT,                       -- what to change: product, variant id, image id
    current     TEXT,
    suggestion  TEXT
);

-- What changed on the website between two syncs
CREATE TABLE IF NOT EXISTS changes (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    product_id INTEGER,
    title      TEXT,
    kind       TEXT NOT NULL,                -- new / removed / price / title / availability / type
    detail     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    source     TEXT NOT NULL,                -- live / snapshot / simulated
    fetched_at TEXT,
    products   INTEGER,
    variants   INTEGER,
    changes    INTEGER
);
