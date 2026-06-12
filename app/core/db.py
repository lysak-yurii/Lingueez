"""Database schema initialization and small direct-SQL helpers.

The schema is byte-compatible with the original app's dictionary.db so
existing databases (and the Supabase mirror) keep working unchanged.
"""
import logging
import sqlite3

DB_PATH = 'dictionary.db'


def _ensure_column(cursor, table, column, decl):
    """Additive migration: add a column to pre-existing databases."""
    cols = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        logging.info("Added column %s.%s", table, column)


def initialize_database(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS words (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            RowNumber INTEGER,
            Source Text,
            Definition Text,
            Definition2 Text,
            Status TEXT,
            Language1 TEXT,
            Word1 TEXT,
            Language2 TEXT,
            Word2 TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            edited_at DATETIME,
            favorite BOOLEAN DEFAULT 0,
            UNIQUE(Word1, Word2)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS texts (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            RowNumber INTEGER,
            Title Text,
            Words Text,
            Text Text,
            Language TEXT,
            Category TEXT,
            Level TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            edited_at DATETIME,
            UNIQUE(ID)
        )
    ''')

    _ensure_column(cursor, 'texts', 'Level', 'TEXT')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name TEXT UNIQUE NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS word_tags (
            word_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            FOREIGN KEY (word_id) REFERENCES words(ID),
            FOREIGN KEY (tag_id) REFERENCES tags(tag_id),
            PRIMARY KEY (word_id, tag_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            synced_at DATETIME,
            UNIQUE(table_name, record_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_type TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            operation_data TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            synced_at DATETIME
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_deletions_table_record ON sync_deletions(table_name, record_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_deletions_synced ON sync_deletions(synced_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_queue_synced ON sync_queue(synced_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_queue_table_record ON sync_queue(table_name, record_id)')

    conn.commit()
    conn.close()
    logging.info("Database initialized successfully.")


def get_all_tags(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT tag_name FROM tags ORDER BY tag_name COLLATE NOCASE")
    tags = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tags


def get_tags_for_word(word_id, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT tags.tag_name FROM tags
        JOIN word_tags ON tags.tag_id = word_tags.tag_id
        WHERE word_tags.word_id = ?
        ORDER BY tags.tag_name COLLATE NOCASE
    ''', (word_id,))
    tags = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tags


def get_word_ids_for_tag(tag_name, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT word_id FROM word_tags
        JOIN tags ON word_tags.tag_id = tags.tag_id
        WHERE tags.tag_name = ?
    ''', (tag_name,))
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def get_word_ids_matching_tag_query(query, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT word_id FROM word_tags
        JOIN tags ON word_tags.tag_id = tags.tag_id
        WHERE tags.tag_name LIKE ?
    ''', (f'%{query}%',))
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def get_tag_usage_counts(db_path=DB_PATH):
    """Return {tag_name: usage_count} across all words."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT tags.tag_name, COUNT(word_tags.word_id)
        FROM tags LEFT JOIN word_tags ON tags.tag_id = word_tags.tag_id
        GROUP BY tags.tag_id ORDER BY tags.tag_name COLLATE NOCASE
    ''')
    counts = dict(cursor.fetchall())
    conn.close()
    return counts
