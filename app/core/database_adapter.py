# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Additional terms under AGPL-3.0 section 7 apply to this program; see the
# NOTICE file distributed with this source for details.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import sqlite3
from typing import Optional, List, Dict, Any
from app.core.supabase_client import SupabaseClient, get_supabase
import logging
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()


class DatabaseAdapter:
    """Unified database interface supporting both SQLite and Supabase."""
    
    def __init__(self, use_cloud: bool = True):
        """
        Initialize database adapter.
        
        Args:
            use_cloud: If True, attempt to use Supabase. Falls back to SQLite if unavailable.
        """
        self.use_cloud = use_cloud
        self.local_db = 'dictionary.db'
        # Share the process-wide client so the signed-in token (set by
        # AuthManager) applies to direct CRUD here, not just to SyncManager.
        self.supabase = get_supabase() if use_cloud else None
        self.cloud_available = self.supabase.is_connected() if self.supabase else False
        
        if use_cloud and not self.cloud_available:
            logging.warning("Supabase not available, using local SQLite only")
        
        # Ensure sync tables exist
        self._ensure_sync_tables()
    
    def _use_cloud(self) -> bool:
        """Check if cloud should be used for this operation."""
        return self.use_cloud and self.cloud_available

    def set_use_cloud(self, enabled: bool):
        """Enable/disable cloud use at runtime without replacing the adapter
        (pages and dialogs hold a reference to this instance). When enabling,
        always refresh the client from the current environment so changed
        Supabase credentials take effect without an app restart."""
        self.use_cloud = enabled
        if enabled:
            if self.supabase is None:
                self.supabase = get_supabase()
            else:
                self.supabase.reconfigure()
        self.cloud_available = self.supabase.is_connected() if self.supabase else False
    
    def _ensure_sync_tables(self):
        """Ensure sync tracking tables exist in the database."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # Create sync_deletions table
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
            
            # Create sync_queue table
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
            
            # Create sync_metadata table for tracking sync state
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create sync_lock table for preventing concurrent syncs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sync_lock (
                    id INTEGER PRIMARY KEY,
                    locked_at DATETIME,
                    locked_by TEXT,
                    expires_at DATETIME
                )
            ''')

            # Create bin_items table (local trash; see db.initialize_database)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bin_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name TEXT NOT NULL,
                    record_id INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    tags TEXT,
                    deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(table_name, record_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_bin_items_deleted ON bin_items(deleted_at)')

            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_deletions_table_record ON sync_deletions(table_name, record_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_deletions_synced ON sync_deletions(synced_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_queue_synced ON sync_queue(synced_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_queue_table_record ON sync_queue(table_name, record_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_metadata_key ON sync_metadata(key)')
            
            conn.commit()
        except Exception as e:
            logging.error(f"Error ensuring sync tables exist: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _track_deletion(self, table_name: str, record_id: int):
        """Track a deletion in sync_deletions table."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # Insert or replace deletion record
            cursor.execute('''
                INSERT OR REPLACE INTO sync_deletions (table_name, record_id, deleted_at, synced_at)
                VALUES (?, ?, datetime('now'), NULL)
            ''', (table_name, record_id))
            conn.commit()
        except Exception as e:
            logging.error(f"Error tracking deletion: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _queue_operation(self, operation_type: str, table_name: str, record_id: int, operation_data: Optional[Dict[str, Any]] = None):
        """Queue an operation for later syncing."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            operation_data_json = json.dumps(operation_data) if operation_data else None
            cursor.execute('''
                INSERT INTO sync_queue (operation_type, table_name, record_id, operation_data, created_at, synced_at)
                VALUES (?, ?, ?, ?, datetime('now'), NULL)
            ''', (operation_type, table_name, record_id, operation_data_json))
            conn.commit()
        except Exception as e:
            logging.error(f"Error queueing operation: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    # ----------------------------------------------------------------- #
    # Local trash ("Bin"). Stashes deleted rows so they can be restored
    # without cloud sync; see app/core/db.py for the schema.
    # ----------------------------------------------------------------- #

    def _bin_capture(self, table_name: str, record_id: int,
                     payload: Dict[str, Any], tags: Optional[List[str]] = None):
        """Stash a deleted row's full payload (and tags) into the local Bin."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO bin_items
                    (table_name, record_id, payload, tags, deleted_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            ''', (table_name, record_id, json.dumps(payload),
                  json.dumps(tags) if tags else None))
            conn.commit()
        except Exception as e:
            logging.error(f"Error capturing {table_name} {record_id} to bin: {e}")
            conn.rollback()
        finally:
            conn.close()

    def _bin_get(self, table_name: str, record_id: int) -> Optional[Dict[str, Any]]:
        """Return the stored Bin entry for a record, or None."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bin_items WHERE table_name = ? AND record_id = ?",
            (table_name, record_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def _bin_remove(self, table_name: str, record_id: int):
        """Drop a Bin entry (after restore or permanent delete)."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM bin_items WHERE table_name = ? AND record_id = ?",
                (table_name, record_id))
            conn.commit()
        finally:
            conn.close()

    def _live_word_conflict(self, payload: Dict[str, Any]) -> bool:
        """True if a live word already uses this payload's (Word1, Word2)."""
        word1 = payload.get('Word1', payload.get('word1'))
        word2 = payload.get('Word2', payload.get('word2'))
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM words WHERE Word1 IS ? AND Word2 IS ? LIMIT 1",
                       (word1, word2))
        hit = cursor.fetchone() is not None
        conn.close()
        return hit

    def get_binned_items(self, table_name: str) -> List[Dict[str, Any]]:
        """Return locally-binned rows for the Bin window.

        Each item is the stored payload dict with ``deleted_at`` overlaid from
        the bin row, matching the shape the Bin UI already expects.
        """
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT record_id, payload, deleted_at FROM bin_items "
            "WHERE table_name = ? ORDER BY deleted_at DESC", (table_name,))
        rows = cursor.fetchall()
        conn.close()
        items = []
        for row in rows:
            try:
                data = json.loads(row['payload'])
            except (TypeError, ValueError):
                continue
            data['deleted_at'] = row['deleted_at']
            items.append(data)
        return items

    def purge_old_binned_items(self, grace_days: int) -> int:
        """Permanently drop Bin entries deleted more than ``grace_days`` ago."""
        cutoff = (datetime.now() - timedelta(days=max(0, grace_days))).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM bin_items WHERE deleted_at < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount or 0
        except Exception as e:
            logging.error(f"Error purging old bin items: {e}")
            conn.rollback()
            return 0
        finally:
            conn.close()

    def count_old_binned_items(self, grace_days: int) -> int:
        """Count Bin entries deleted more than ``grace_days`` ago."""
        cutoff = (datetime.now() - timedelta(days=max(0, grace_days))).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bin_items WHERE deleted_at < ?", (cutoff,))
        n = cursor.fetchone()[0]
        conn.close()
        return int(n or 0)

    def delete_binned_item(self, table_name: str, record_id: int) -> bool:
        """Permanently delete a binned item: drop the local copy and, when
        connected, hard-delete the cloud soft-delete too."""
        self._bin_remove(table_name, record_id)
        if self._use_cloud():
            try:
                if table_name == 'words':
                    self.supabase.hard_delete_word(record_id)
                else:
                    self.supabase.hard_delete_text(record_id)
            except Exception as e:
                logging.warning(f"Cloud hard-delete of {table_name} {record_id} failed: {e}")
        return True

    def _get_pending_deletions(self) -> List[Dict[str, Any]]:
        """Get all pending deletions that need to be synced."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT table_name, record_id, deleted_at
                FROM sync_deletions
                WHERE synced_at IS NULL
                ORDER BY deleted_at ASC
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logging.error(f"Error getting pending deletions: {e}")
            return []
        finally:
            conn.close()
    
    def _get_pending_operations(self) -> List[Dict[str, Any]]:
        """Get all pending operations that need to be synced."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT id, operation_type, table_name, record_id, operation_data, created_at
                FROM sync_queue
                WHERE synced_at IS NULL
                ORDER BY created_at ASC
            ''')
            rows = cursor.fetchall()
            result = []
            for row in rows:
                op = dict(row)
                if op.get('operation_data'):
                    try:
                        op['operation_data'] = json.loads(op['operation_data'])
                    except json.JSONDecodeError:
                        op['operation_data'] = None
                result.append(op)
            return result
        except Exception as e:
            logging.error(f"Error getting pending operations: {e}")
            return []
        finally:
            conn.close()
    
    def _mark_deletion_synced(self, table_name: str, record_id: int):
        """Mark a deletion as synced."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE sync_deletions
                SET synced_at = datetime('now')
                WHERE table_name = ? AND record_id = ?
            ''', (table_name, record_id))
            conn.commit()
        except Exception as e:
            logging.error(f"Error marking deletion as synced: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _mark_operation_synced(self, queue_id: int):
        """Mark an operation as synced."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE sync_queue
                SET synced_at = datetime('now')
                WHERE id = ?
            ''', (queue_id,))
            conn.commit()
        except Exception as e:
            logging.error(f"Error marking operation as synced: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _clear_synced_operations(self, days_old: int = 7):
        """Clear old synced operations and deletions to keep database clean."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # Delete old synced operations
            cursor.execute('''
                DELETE FROM sync_queue
                WHERE synced_at IS NOT NULL
                AND date(synced_at) < date('now', '-' || ? || ' days')
            ''', (days_old,))
            
            # Delete old synced deletions
            cursor.execute('''
                DELETE FROM sync_deletions
                WHERE synced_at IS NOT NULL
                AND date(synced_at) < date('now', '-' || ? || ' days')
            ''', (days_old,))
            
            conn.commit()
        except Exception as e:
            logging.error(f"Error clearing synced operations: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def get_sync_metadata(self, key: str) -> Optional[str]:
        """Get sync metadata value by key."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT value FROM sync_metadata WHERE key = ?', (key,))
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logging.error(f"Error getting sync metadata: {e}")
            return None
        finally:
            conn.close()
    
    def set_sync_metadata(self, key: str, value: str):
        """Set sync metadata value by key."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
            ''', (key, value))
            conn.commit()
        except Exception as e:
            logging.error(f"Error setting sync metadata: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def acquire_sync_lock(self, lock_id: str = 'default', timeout_seconds: int = 300) -> bool:
        """Acquire sync lock to prevent concurrent syncs. Returns True if lock acquired."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # First, clean up expired locks
            cursor.execute('''
                DELETE FROM sync_lock
                WHERE expires_at < datetime('now')
            ''')
            
            # Try to acquire lock
            expires_at = (datetime.now() + timedelta(seconds=timeout_seconds)).isoformat()
            
            cursor.execute('''
                INSERT INTO sync_lock (id, locked_at, locked_by, expires_at)
                VALUES (1, datetime('now'), ?, ?)
            ''', (lock_id, expires_at))
            
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Lock already exists, check if expired
            cursor.execute('SELECT expires_at FROM sync_lock WHERE id = 1')
            result = cursor.fetchone()
            if result and result[0]:
                expires_dt = datetime.fromisoformat(result[0])
                if datetime.now() > expires_dt:
                    # Lock expired, remove it and try again
                    cursor.execute('DELETE FROM sync_lock WHERE id = 1')
                    cursor.execute('''
                        INSERT INTO sync_lock (id, locked_at, locked_by, expires_at)
                        VALUES (1, datetime('now'), ?, ?)
                    ''', (lock_id, expires_at))
                    conn.commit()
                    return True
            # Lock is still valid
            conn.rollback()
            return False
        except Exception as e:
            logging.error(f"Error acquiring sync lock: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def release_sync_lock(self):
        """Release sync lock."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM sync_lock WHERE id = 1')
            conn.commit()
        except Exception as e:
            logging.error(f"Error releasing sync lock: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def is_sync_lock_held(self) -> bool:
        """Check if sync lock is currently held (without acquiring it). Returns True if lock is held."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # First, clean up expired locks
            cursor.execute('''
                DELETE FROM sync_lock
                WHERE expires_at < datetime('now')
            ''')
            conn.commit()
            
            # Check if lock exists
            cursor.execute('SELECT id FROM sync_lock WHERE id = 1')
            result = cursor.fetchone()
            return result is not None
        except Exception as e:
            logging.error(f"Error checking sync lock status: {e}")
            return False
        finally:
            conn.close()
    
    # Words operations
    def get_words(self) -> List[Dict[str, Any]]:
        """Get all words. Reads from Supabase if cloud is available and local DB doesn't exist, otherwise from local SQLite."""
        # Check if local database exists and has the words table
        import os
        local_db_exists = os.path.exists(self.local_db)
        
        if local_db_exists:
            try:
                # Try to read from local SQLite first
                return self._get_words_sqlite()
            except (sqlite3.OperationalError, FileNotFoundError):
                # Local DB doesn't have the table or doesn't exist - use cloud if available
                if self._use_cloud():
                    return self.supabase.get_words()
                return []
        else:
            # No local DB - use cloud if available
            if self._use_cloud():
                return self.supabase.get_words()
            return []
    
    def _get_words_sqlite(self) -> List[Dict[str, Any]]:
        """Get all words from SQLite."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # Order by created_at descending, then by ID descending for consistent ordering
        cursor.execute("SELECT * FROM words ORDER BY created_at DESC, ID DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_word(self, word_id: int) -> Optional[Dict[str, Any]]:
        """Get a single word by ID. Reads from Supabase if cloud is available and local DB doesn't exist, otherwise from local SQLite."""
        import os
        local_db_exists = os.path.exists(self.local_db)
        
        if local_db_exists:
            try:
                return self._get_word_sqlite(word_id)
            except (sqlite3.OperationalError, FileNotFoundError):
                if self._use_cloud():
                    return self.supabase.get_word(word_id)
                return None
        else:
            if self._use_cloud():
                return self.supabase.get_word(word_id)
            return None
    
    def _get_word_sqlite(self, word_id: int) -> Optional[Dict[str, Any]]:
        """Get a single word from SQLite."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM words WHERE ID = ?", (word_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def insert_word(self, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a word. Writes to both local and cloud if available."""
        # Always write to local first
        local_result = self._insert_word_sqlite(word_data)
        
        if local_result:
            word_id = local_result['ID']
            
            # If cloud is available, try to sync immediately
            if self._use_cloud():
                try:
                    # Use upsert logic: find by content, update if found, insert if not (auto-generate ID)
                    # This prevents sequence sync issues and handles duplicate words correctly
                    cloud_result = self.supabase.upsert_word(word_data)
                    if cloud_result:
                        # Store cloud_id from cloud result
                        cloud_id = cloud_result.get('ID') or cloud_result.get('id')
                        if cloud_id:
                            self._update_cloud_id_sqlite(word_id, cloud_id)
                            logging.debug(f"Stored cloud_id={cloud_id} for word {word_id}")
                        return local_result
                    else:
                        # Queue operation for later sync
                        self._queue_operation('INSERT', 'words', word_id, local_result)
                        logging.warning(f"Failed to sync word {word_id} to cloud, queued for later")
                except Exception as e:
                    # Queue operation for later sync
                    self._queue_operation('INSERT', 'words', word_id, local_result)
                    logging.warning(f"Error syncing word {word_id} to cloud: {e}, queued for later")
            else:
                # Cloud not available, queue for later sync
                self._queue_operation('INSERT', 'words', word_id, local_result)
                logging.debug(f"Word {word_id} inserted locally, will sync when cloud is available")
        
        return local_result
    
    def _insert_word_sqlite(self, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a word into SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        # Extract fields, handling both dict and direct values
        language1 = word_data.get('Language1', word_data.get('language1'))
        word1 = word_data.get('Word1', word_data.get('word1'))
        language2 = word_data.get('Language2', word_data.get('language2'))
        word2 = word_data.get('Word2', word_data.get('word2'))
        status = word_data.get('Status', word_data.get('status'))
        source = word_data.get('Source', word_data.get('source', ''))
        
        # Explicitly set created_at to ensure consistent ordering
        from datetime import datetime
        created_at = word_data.get('created_at')
        if not created_at:
            created_at = datetime.now(timezone.utc).isoformat()
        
        cursor.execute('''
            INSERT INTO words (Language1, Word1, Language2, Word2, Status, Source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (language1, word1, language2, word2, status, source, created_at))
        
        word_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Return the inserted word
        return self._get_word_sqlite(word_id)
    
    def _compare_timestamps(self, ts1: Optional[str], ts2: Optional[str]) -> int:
        """Compare two timestamps. Returns -1 if ts1 < ts2, 0 if equal, 1 if ts1 > ts2."""
        if not ts1 and not ts2:
            return 0
        if not ts1:
            return -1
        if not ts2:
            return 1
        
        try:
            # Try ISO format
            ts1_clean = ts1.replace('Z', '+00:00') if 'Z' in str(ts1) else str(ts1)
            ts2_clean = ts2.replace('Z', '+00:00') if 'Z' in str(ts2) else str(ts2)
            dt1 = datetime.fromisoformat(ts1_clean)
            dt2 = datetime.fromisoformat(ts2_clean)
            
            if dt1 < dt2:
                return -1
            elif dt1 > dt2:
                return 1
            return 0
        except (ValueError, AttributeError):
            try:
                # Try SQLite format
                dt1 = datetime.strptime(str(ts1), '%Y-%m-%d %H:%M:%S')
                dt2 = datetime.strptime(str(ts2), '%Y-%m-%d %H:%M:%S')
                if dt1 < dt2:
                    return -1
                elif dt1 > dt2:
                    return 1
                return 0
            except (ValueError, AttributeError):
                # Fallback to string comparison
                if str(ts1) < str(ts2):
                    return -1
                elif str(ts1) > str(ts2):
                    return 1
                return 0
    
    def update_word(self, word_id: int, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a word. Updates both local and cloud if available.
        
        Uses cloud_id if available for reliable cloud word identification.
        Before updating, checks if cloud has a newer version and syncs it first
        to prevent conflicts when word was edited in web app.
        """
        # Get OLD word data BEFORE updating (needed to find cloud word)
        old_word_data = self._get_word_sqlite(word_id)
        
        # Store cloud word ID if we find it - we'll need it to update regardless of content changes
        cloud_word_id = None
        
        # If cloud is available, check for newer version and sync it first
        if self._use_cloud() and old_word_data:
            try:
                # Get cloud_id from local word (most reliable way to find cloud word)
                cloud_word_id = old_word_data.get('cloud_id')
                
                if cloud_word_id:
                    # First try to get cloud word by cloud_id (most reliable)
                    cloud_word = self.supabase.get_word(cloud_word_id)
                    if cloud_word:
                        # Compare timestamps to see if cloud has newer version
                        cloud_ts = cloud_word.get('edited_at') or cloud_word.get('created_at')
                        local_ts = old_word_data.get('edited_at') or old_word_data.get('created_at')
                        
                        if cloud_ts and local_ts:
                            comparison = self._compare_timestamps(cloud_ts, local_ts)
                            if comparison > 0:  # Cloud is newer
                                # Cloud has newer version - sync it to local first
                                logging.info(f"Cloud has newer version of word {word_id}, syncing before edit")
                                # Update local with cloud version (merge cloud changes)
                                # Preserve local created_at if it's older (to maintain order)
                                local_created_at = old_word_data.get('created_at')
                                cloud_created_at = cloud_word.get('created_at')
                                
                                # Use cloud data but preserve local created_at if it's older
                                merged_data = cloud_word.copy()
                                if local_created_at and cloud_created_at:
                                    created_comparison = self._compare_timestamps(local_created_at, cloud_created_at)
                                    if created_comparison < 0:  # Local created_at is older
                                        merged_data['created_at'] = local_created_at
                                
                                # Update local with merged cloud data (preserve cloud_id)
                                merged_data['cloud_id'] = cloud_word_id
                                self._update_word_sqlite(word_id, merged_data)
                                # Update old_word_data to reflect synced version
                                old_word_data = self._get_word_sqlite(word_id)
                                logging.info(f"Synced word {word_id} from cloud before applying edit")
                    else:
                        # cloud_id exists but word not found in cloud - might have been deleted
                        logging.warning(f"cloud_id {cloud_word_id} found but word not in cloud, clearing cloud_id")
                        cloud_word_id = None
                
                # No cloud_id - fall back to content-based matching
                if not cloud_word_id:
                    old_language1 = old_word_data.get('Language1')
                    old_word1 = old_word_data.get('Word1')
                    old_language2 = old_word_data.get('Language2')
                    old_word2 = old_word_data.get('Word2')
                    
                    if all([old_language1, old_word1, old_language2, old_word2]):
                        # Find cloud word by old content
                        cloud_word = self.supabase.find_word_by_content(old_language1, old_word1, old_language2, old_word2)
                        if cloud_word:
                            # Store the cloud word ID - we'll need it to update after edit
                            cloud_word_id = cloud_word.get('ID') or cloud_word.get('id')
                            
                            # Compare timestamps to see if cloud has newer version
                            cloud_ts = cloud_word.get('edited_at') or cloud_word.get('created_at')
                            local_ts = old_word_data.get('edited_at') or old_word_data.get('created_at')
                            
                            if cloud_ts and local_ts:
                                comparison = self._compare_timestamps(cloud_ts, local_ts)
                                if comparison > 0:  # Cloud is newer
                                    # Cloud has newer version - sync it to local first
                                    logging.info(f"Cloud has newer version of word {word_id}, syncing before edit")
                                    # Update local with cloud version (merge cloud changes)
                                    local_created_at = old_word_data.get('created_at')
                                    cloud_created_at = cloud_word.get('created_at')
                                    
                                    # Use cloud data but preserve local created_at if it's older
                                    merged_data = cloud_word.copy()
                                    if local_created_at and cloud_created_at:
                                        created_comparison = self._compare_timestamps(local_created_at, cloud_created_at)
                                        if created_comparison < 0:  # Local created_at is older
                                            merged_data['created_at'] = local_created_at
                                    
                                    # Store cloud_id for future updates
                                    merged_data['cloud_id'] = cloud_word_id
                                    self._update_word_sqlite(word_id, merged_data)
                                    # Update old_word_data to reflect synced version
                                    old_word_data = self._get_word_sqlite(word_id)
                                    logging.info(f"Synced word {word_id} from cloud before applying edit")
            except Exception as e:
                logging.warning(f"Error checking for cloud updates before edit: {e}")
        
        # Now update local with user's changes
        local_result = self._update_word_sqlite(word_id, word_data)
        
        if local_result:
            # If cloud is available, try to sync immediately
            if self._use_cloud():
                try:
                    # First try to use cloud_id (most reliable)
                    if cloud_word_id:
                        logging.debug(f"Updating cloud word {cloud_word_id} directly (using cloud_id)")
                        cloud_result = self.supabase.update_word(cloud_word_id, word_data)
                        if cloud_result:
                            # Ensure cloud_id is stored (in case it wasn't before)
                            if not old_word_data or not old_word_data.get('cloud_id'):
                                self._update_cloud_id_sqlite(word_id, cloud_word_id)
                            return local_result
                    
                    # Fallback: Try to find cloud word using content
                    # Use the content from old_word_data (before user's edit, after potential sync)
                    if old_word_data:
                        old_language1 = old_word_data.get('Language1')
                        old_word1 = old_word_data.get('Word1')
                        old_language2 = old_word_data.get('Language2')
                        old_word2 = old_word_data.get('Word2')
                        
                        if all([old_language1, old_word1, old_language2, old_word2]):
                            # Find cloud word by old content (before user's edit)
                            cloud_word = self.supabase.find_word_by_content(old_language1, old_word1, old_language2, old_word2)
                            if cloud_word:
                                cloud_id = cloud_word.get('ID') or cloud_word.get('id')
                                if cloud_id:
                                    # Update the cloud word using its ID with NEW data
                                    cloud_result = self.supabase.update_word(cloud_id, word_data)
                                    if cloud_result:
                                        # Store cloud_id for future updates
                                        self._update_cloud_id_sqlite(word_id, cloud_id)
                                        return local_result
                                else:
                                    logging.warning(f"Found cloud word but couldn't get ID for update {word_id}")
                            else:
                                # Old word not found in cloud - try by new content
                                new_language1 = local_result.get('Language1') or word_data.get('Language1') or word_data.get('language1')
                                new_word1 = local_result.get('Word1') or word_data.get('Word1') or word_data.get('word1')
                                new_language2 = local_result.get('Language2') or word_data.get('Language2') or word_data.get('language2')
                                new_word2 = local_result.get('Word2') or word_data.get('Word2') or word_data.get('word2')
                                
                                if all([new_language1, new_word1, new_language2, new_word2]):
                                    cloud_word = self.supabase.find_word_by_content(new_language1, new_word1, new_language2, new_word2)
                                    if cloud_word:
                                        cloud_id = cloud_word.get('ID') or cloud_word.get('id')
                                        if cloud_id:
                                            cloud_result = self.supabase.update_word(cloud_id, word_data)
                                            if cloud_result:
                                                # Store cloud_id for future updates
                                                self._update_cloud_id_sqlite(word_id, cloud_id)
                                                return local_result
                                
                                # Not found by old or new content - use upsert (will insert if truly new)
                                logging.warning(f"Word {word_id} not found in cloud by any method, using upsert (may create duplicate)")
                                cloud_result = self.supabase.upsert_word(word_data)
                                if cloud_result:
                                    # Store cloud_id from result
                                    result_cloud_id = cloud_result.get('ID') or cloud_result.get('id')
                                    if result_cloud_id:
                                        self._update_cloud_id_sqlite(word_id, result_cloud_id)
                                    return local_result
                        else:
                            logging.warning(f"Cannot find cloud word by old content (missing fields) for update {word_id}")
                    else:
                        logging.warning(f"Could not get old word data for {word_id} before update")
                    
                    # If we couldn't find by content or update failed, queue for later
                    self._queue_operation('UPDATE', 'words', word_id, local_result)
                    logging.warning(f"Failed to sync word {word_id} update to cloud, queued for later")
                except Exception as e:
                    # Queue operation for later sync
                    self._queue_operation('UPDATE', 'words', word_id, local_result)
                    logging.warning(f"Error syncing word {word_id} update to cloud: {e}, queued for later")
            else:
                # Cloud not available, queue for later sync
                self._queue_operation('UPDATE', 'words', word_id, local_result)
                logging.debug(f"Word {word_id} updated locally, will sync when cloud is available")
        
        return local_result
    
    def _update_word_sqlite(self, word_id: int, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a word in SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        # Build update query dynamically
        updates = []
        values = []
        
        for key in ['Language1', 'Word1', 'Language2', 'Word2', 'Status', 'Source', 
                   'Definition', 'Definition2', 'favorite', 'cloud_id']:
            if key in word_data:
                updates.append(f"{key} = ?")
                values.append(word_data[key])
        
        # Always update edited_at
        from datetime import datetime
        updates.append("edited_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(word_id)
        
        if updates:
            query = f"UPDATE words SET {', '.join(updates)} WHERE ID = ?"
            cursor.execute(query, values)
            conn.commit()
        
        conn.close()
        return self._get_word_sqlite(word_id)
    
    def _update_cloud_id_sqlite(self, word_id: int, cloud_id: int) -> None:
        """Update cloud_id for a word in SQLite.
        
        Args:
            word_id: Local word ID
            cloud_id: Cloud word ID to store
        """
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute("UPDATE words SET cloud_id = ? WHERE ID = ?", (cloud_id, word_id))
            conn.commit()
            logging.debug(f"Updated cloud_id={cloud_id} for local word {word_id}")
        except Exception as e:
            logging.error(f"Error updating cloud_id for word {word_id}: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def delete_word(self, word_id: int) -> bool:
        """Delete a word. Deletes from both local and cloud if available.
        
        Uses cloud_id if available for reliable cloud word identification.
        """
        # Get word data before deleting (needed for cloud_id or content-based lookup in cloud)
        word_data = self._get_word_sqlite(word_id)

        # Stash the row (and its tags) in the local Bin so it can be restored
        # even without cloud sync.
        if word_data:
            tag_names = [t.get('tag_name') for t in self._get_word_tags_sqlite(word_id)
                         if t.get('tag_name')]
            self._bin_capture('words', word_id, word_data, tag_names)

        # Always delete from local
        local_success = self._delete_word_sqlite(word_id)

        if local_success:
            # Track deletion for sync
            self._track_deletion('words', word_id)
            
            # If cloud is available, try to delete from cloud immediately
            if self._use_cloud():
                try:
                    if word_data:
                        # First try to use cloud_id (most reliable)
                        cloud_id = word_data.get('cloud_id')
                        
                        if cloud_id:
                            # Use cloud_id directly
                            cloud_success = self.supabase.delete_word(cloud_id)
                            if cloud_success:
                                # Mark deletion as synced
                                self._mark_deletion_synced('words', word_id)
                            else:
                                # Queue deletion for later sync
                                logging.warning(f"Failed to sync word {word_id} deletion to cloud using cloud_id {cloud_id}, queued for later")
                        else:
                            # Fallback to content-based lookup
                            language1 = word_data.get('Language1')
                            word1 = word_data.get('Word1')
                            language2 = word_data.get('Language2')
                            word2 = word_data.get('Word2')
                            
                            if all([language1, word1, language2, word2]):
                                cloud_word = self.supabase.find_word_by_content(language1, word1, language2, word2)
                                if cloud_word:
                                    cloud_id = cloud_word.get('ID') or cloud_word.get('id')
                                    if cloud_id:
                                        cloud_success = self.supabase.delete_word(cloud_id)
                                        if cloud_success:
                                            # Mark deletion as synced
                                            self._mark_deletion_synced('words', word_id)
                                        else:
                                            # Queue deletion for later sync
                                            logging.warning(f"Failed to sync word {word_id} deletion to cloud, queued for later")
                                else:
                                    # Word not found in cloud, consider it already deleted
                                    logging.debug(f"Word {word_id} not found in cloud, considering deletion synced")
                                    self._mark_deletion_synced('words', word_id)
                            else:
                                logging.warning(f"Cannot find cloud word by content (missing fields), queued for later")
                    else:
                        logging.warning(f"Could not get word data for {word_id} before deletion, queued for later")
                except Exception as e:
                    logging.warning(f"Error syncing word {word_id} deletion to cloud: {e}, queued for later")
            else:
                # Cloud not available, deletion is tracked and will be synced later
                logging.debug(f"Word {word_id} deleted locally, will sync when cloud is available")
        
        return local_success
    
    def _delete_word_sqlite(self, word_id: int) -> bool:
        """Delete a word from SQLite, including its tag links."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM word_tags WHERE word_id = ?", (word_id,))
        cursor.execute("DELETE FROM words WHERE ID = ?", (word_id,))
        conn.commit()
        conn.close()
        return True
    
    # Texts operations
    def get_texts(self) -> List[Dict[str, Any]]:
        """Get all texts. Reads from Supabase if cloud is available and local DB doesn't exist, otherwise from local SQLite."""
        import os
        local_db_exists = os.path.exists(self.local_db)
        
        if local_db_exists:
            try:
                return self._get_texts_sqlite()
            except (sqlite3.OperationalError, FileNotFoundError):
                if self._use_cloud():
                    return self.supabase.get_texts()
                return []
        else:
            if self._use_cloud():
                return self.supabase.get_texts()
            return []
    
    def _get_texts_sqlite(self) -> List[Dict[str, Any]]:
        """Get all texts from SQLite."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM texts")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_text(self, text_id: int) -> Optional[Dict[str, Any]]:
        """Get a single text by ID. Reads from Supabase if cloud is available and local DB doesn't exist, otherwise from local SQLite."""
        import os
        local_db_exists = os.path.exists(self.local_db)
        
        if local_db_exists:
            try:
                return self._get_text_sqlite(text_id)
            except (sqlite3.OperationalError, FileNotFoundError):
                if self._use_cloud():
                    return self.supabase.get_text(text_id)
                return None
        else:
            if self._use_cloud():
                return self.supabase.get_text(text_id)
            return None
    
    def _get_text_sqlite(self, text_id: int) -> Optional[Dict[str, Any]]:
        """Get a single text from SQLite."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM texts WHERE ID = ?", (text_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def insert_text(self, text_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a text. Writes to both local and cloud if available."""
        local_result = self._insert_text_sqlite(text_data)
        
        if local_result:
            text_id = local_result['ID']
            
            # If cloud is available, try to sync immediately
            if self._use_cloud():
                try:
                    cloud_data = text_data.copy()
                    cloud_data['ID'] = text_id
                    
                    # Check if text already exists in cloud (might happen during sync)
                    existing_text = self.supabase.get_text(text_id)
                    if existing_text:
                        # Text exists, update it instead of inserting
                        cloud_result = self.supabase.update_text(text_id, cloud_data)
                        if cloud_result:
                            return local_result
                        else:
                            self._queue_operation('UPDATE', 'texts', text_id, local_result)
                            logging.warning(f"Failed to sync text {text_id} update to cloud, queued for later")
                    else:
                        # Text doesn't exist, insert it with preserved ID
                        cloud_result = self.supabase.insert_text(cloud_data, preserve_id=True)
                        if cloud_result:
                            return local_result
                        else:
                            self._queue_operation('INSERT', 'texts', text_id, local_result)
                            logging.warning(f"Failed to sync text {text_id} to cloud, queued for later")
                except Exception as e:
                    # Queue operation for later sync
                    self._queue_operation('INSERT', 'texts', text_id, local_result)
                    logging.warning(f"Error syncing text {text_id} to cloud: {e}, queued for later")
            else:
                # Cloud not available, queue for later sync
                self._queue_operation('INSERT', 'texts', text_id, local_result)
                logging.debug(f"Text {text_id} inserted locally, will sync when cloud is available")
        
        return local_result
    
    def _insert_text_sqlite(self, text_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a text into SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        row_number = text_data.get('RowNumber', text_data.get('row_number'))
        title = text_data.get('Title', text_data.get('title'))
        text = text_data.get('Text', text_data.get('text'))
        words = text_data.get('Words', text_data.get('words'))
        language = text_data.get('Language', text_data.get('language'))
        category = text_data.get('Category', text_data.get('category'))
        level = text_data.get('Level', text_data.get('level'))

        cursor.execute('''
            INSERT INTO texts (RowNumber, Title, Text, Words, Language, Category, Level, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (row_number, title, text, words, language, category, level))
        
        text_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return self._get_text_sqlite(text_id)
    
    def update_text(self, text_id: int, text_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a text. Updates both local and cloud if available."""
        local_result = self._update_text_sqlite(text_id, text_data)
        
        if local_result:
            # If cloud is available, try to sync immediately
            if self._use_cloud():
                try:
                    cloud_result = self.supabase.update_text(text_id, text_data)
                    if cloud_result:
                        return local_result
                    else:
                        # Queue operation for later sync
                        self._queue_operation('UPDATE', 'texts', text_id, local_result)
                        logging.warning(f"Failed to sync text {text_id} update to cloud, queued for later")
                except Exception as e:
                    # Queue operation for later sync
                    self._queue_operation('UPDATE', 'texts', text_id, local_result)
                    logging.warning(f"Error syncing text {text_id} update to cloud: {e}, queued for later")
            else:
                # Cloud not available, queue for later sync
                self._queue_operation('UPDATE', 'texts', text_id, local_result)
                logging.debug(f"Text {text_id} updated locally, will sync when cloud is available")
        
        return local_result
    
    def _update_text_sqlite(self, text_id: int, text_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a text in SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        updates = []
        values = []
        
        for key in ['RowNumber', 'Title', 'Text', 'Words', 'Language', 'Category', 'Level']:
            if key in text_data:
                updates.append(f"{key} = ?")
                values.append(text_data[key])
        
        from datetime import datetime
        updates.append("edited_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(text_id)
        
        if updates:
            query = f"UPDATE texts SET {', '.join(updates)} WHERE ID = ?"
            cursor.execute(query, values)
            conn.commit()
        
        conn.close()
        return self._get_text_sqlite(text_id)
    
    def delete_text(self, text_id: int) -> bool:
        """Delete a text. Deletes from both local and cloud if available."""
        # Stash the row in the local Bin so it can be restored without cloud sync.
        text_data = self._get_text_sqlite(text_id)
        if text_data:
            self._bin_capture('texts', text_id, text_data)

        local_success = self._delete_text_sqlite(text_id)

        if local_success:
            # Track deletion for sync
            self._track_deletion('texts', text_id)
            
            # If cloud is available, try to delete from cloud immediately
            if self._use_cloud():
                try:
                    cloud_success = self.supabase.delete_text(text_id)
                    if cloud_success:
                        # Mark deletion as synced
                        self._mark_deletion_synced('texts', text_id)
                    else:
                        # Queue deletion for later sync
                        logging.warning(f"Failed to sync text {text_id} deletion to cloud, queued for later")
                except Exception as e:
                    logging.warning(f"Error syncing text {text_id} deletion to cloud: {e}, queued for later")
            else:
                # Cloud not available, deletion is tracked and will be synced later
                logging.debug(f"Text {text_id} deleted locally, will sync when cloud is available")
        
        return local_success
    
    def _delete_text_sqlite(self, text_id: int) -> bool:
        """Delete a text from SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM texts WHERE ID = ?", (text_id,))
        conn.commit()
        conn.close()
        return True
    
    def restore_word(self, word_id: int) -> bool:
        """Restore a deleted word.

        Store-first: if the word is in the local Bin, re-insert it (preserved ID
        + tags) from the stored payload so it works without cloud sync. A live
        word then propagates to the cloud through the normal sync push. Falls
        back to the cloud bin for cloud-originated soft-deletes not stored locally.

        Args:
            word_id: ID of the word to restore

        Returns:
            True if successful, False otherwise
        """
        try:
            binned = self._bin_get('words', word_id)
            if binned:
                payload = json.loads(binned['payload'])
                if not self._get_word_sqlite(word_id):
                    # A live word with the same Word1/Word2 would violate the
                    # UNIQUE constraint — refuse rather than crash, leaving the
                    # item in the Bin so the user can resolve the duplicate.
                    if self._live_word_conflict(payload):
                        logging.warning(
                            f"Cannot restore word {word_id}: a word with the same "
                            f"text already exists")
                        return False
                    self._insert_word_sqlite_with_id(payload, word_id)
                # Re-link tags captured at deletion time.
                try:
                    tags = json.loads(binned['tags']) if binned.get('tags') else []
                except (TypeError, ValueError):
                    tags = []
                for tag_name in tags:
                    tag_id = self._get_or_create_tag_sqlite(tag_name)
                    if tag_id:
                        self._add_tag_to_word_sqlite(word_id, tag_id)
                # Best-effort: clear the cloud soft-delete if the word exists there.
                if self._use_cloud():
                    try:
                        self.supabase.restore_word(word_id)
                    except Exception as e:
                        logging.debug(f"Cloud restore of word {word_id} skipped: {e}")
                self._bin_remove('words', word_id)
                self._remove_deletion_tracking('words', word_id)
                logging.info(f"Restored word {word_id} from local bin")
                return True

            # Not in the local bin — cloud-originated soft-delete.
            if not self._use_cloud():
                logging.warning(f"Cannot restore word {word_id}: not in local bin and cloud not available")
                return False

            cloud_success = self.supabase.restore_word(word_id)
            if not cloud_success:
                logging.error(f"Failed to restore word {word_id} in cloud")
                return False
            if not self._get_word_sqlite(word_id):
                cloud_word = self.supabase.get_word(word_id)
                if cloud_word:
                    self._insert_word_sqlite_with_id(cloud_word, word_id)
                else:
                    logging.warning(f"Word {word_id} restored in cloud but not found when fetching")
            self._remove_deletion_tracking('words', word_id)
            return True
        except Exception as e:
            logging.error(f"Error restoring word {word_id}: {e}", exc_info=True)
            return False

    def restore_text(self, text_id: int) -> bool:
        """Restore a deleted text (store-first, with cloud fallback).

        See :meth:`restore_word` for the strategy.

        Args:
            text_id: ID of the text to restore

        Returns:
            True if successful, False otherwise
        """
        try:
            binned = self._bin_get('texts', text_id)
            if binned:
                payload = json.loads(binned['payload'])
                if not self._get_text_sqlite(text_id):
                    self._insert_text_sqlite_with_id(payload, text_id)
                if self._use_cloud():
                    try:
                        self.supabase.restore_text(text_id)
                    except Exception as e:
                        logging.debug(f"Cloud restore of text {text_id} skipped: {e}")
                self._bin_remove('texts', text_id)
                self._remove_deletion_tracking('texts', text_id)
                logging.info(f"Restored text {text_id} from local bin")
                return True

            if not self._use_cloud():
                logging.warning(f"Cannot restore text {text_id}: not in local bin and cloud not available")
                return False

            cloud_success = self.supabase.restore_text(text_id)
            if not cloud_success:
                logging.error(f"Failed to restore text {text_id} in cloud")
                return False
            if not self._get_text_sqlite(text_id):
                cloud_text = self.supabase.get_text(text_id)
                if cloud_text:
                    self._insert_text_sqlite_with_id(cloud_text, text_id)
                else:
                    logging.warning(f"Text {text_id} restored in cloud but not found when fetching")
            self._remove_deletion_tracking('texts', text_id)
            return True
        except Exception as e:
            logging.error(f"Error restoring text {text_id}: {e}", exc_info=True)
            return False

    def _remove_deletion_tracking(self, table_name: str, record_id: int):
        """Remove deletion tracking for a restored item."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                DELETE FROM sync_deletions
                WHERE table_name = ? AND record_id = ?
            ''', (table_name, record_id))
            conn.commit()
        except Exception as e:
            logging.warning(f"Error removing deletion tracking: {e}")
        finally:
            conn.close()
    
    def _insert_word_sqlite_with_id(self, word_data: Dict[str, Any], word_id: int) -> Optional[Dict[str, Any]]:
        """Insert a word into SQLite with a specific ID (for restoring)."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        # Extract fields, handling both dict and direct values
        language1 = word_data.get('Language1', word_data.get('language1'))
        word1 = word_data.get('Word1', word_data.get('word1'))
        language2 = word_data.get('Language2', word_data.get('language2'))
        word2 = word_data.get('Word2', word_data.get('word2'))
        status = word_data.get('Status', word_data.get('status'))
        source = word_data.get('Source', word_data.get('source', ''))
        definition = word_data.get('Definition', word_data.get('definition'))
        definition2 = word_data.get('Definition2', word_data.get('definition2'))
        row_number = word_data.get('RowNumber', word_data.get('row_number'))
        favorite = word_data.get('favorite', False)
        
        from datetime import datetime
        created_at = word_data.get('created_at')
        if not created_at:
            created_at = datetime.now(timezone.utc).isoformat()
        edited_at = word_data.get('edited_at')
        
        try:
            cursor.execute('''
                INSERT INTO words (ID, Language1, Word1, Language2, Word2, Status, Source,
                                 Definition, Definition2, RowNumber, favorite, created_at, edited_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (word_id, language1, word1, language2, word2, status, source,
                  definition, definition2, row_number, favorite, created_at, edited_at))
            conn.commit()
        finally:
            conn.close()

        # Return the inserted word
        return self._get_word_sqlite(word_id)
    
    def _insert_text_sqlite_with_id(self, text_data: Dict[str, Any], text_id: int) -> Optional[Dict[str, Any]]:
        """Insert a text into SQLite with a specific ID (for restoring)."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        row_number = text_data.get('RowNumber', text_data.get('row_number'))
        title = text_data.get('Title', text_data.get('title'))
        text = text_data.get('Text', text_data.get('text'))
        words = text_data.get('Words', text_data.get('words'))
        language = text_data.get('Language', text_data.get('language'))
        category = text_data.get('Category', text_data.get('category'))
        level = text_data.get('Level', text_data.get('level'))

        from datetime import datetime
        created_at = text_data.get('created_at')
        if not created_at:
            created_at = datetime.now(timezone.utc).isoformat()
        edited_at = text_data.get('edited_at')

        try:
            cursor.execute('''
                INSERT INTO texts (ID, RowNumber, Title, Text, Words, Language, Category, Level, created_at, edited_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (text_id, row_number, title, text, words, language, category, level, created_at, edited_at))
            conn.commit()
        finally:
            conn.close()

        return self._get_text_sqlite(text_id)
    
    # Tags operations
    def get_tags(self) -> List[Dict[str, Any]]:
        """Get all tags. Reads from Supabase if cloud is available and local DB doesn't exist, otherwise from local SQLite."""
        import os
        local_db_exists = os.path.exists(self.local_db)
        
        if local_db_exists:
            try:
                return self._get_tags_sqlite()
            except (sqlite3.OperationalError, FileNotFoundError):
                if self._use_cloud():
                    return self.supabase.get_tags()
                return []
        else:
            if self._use_cloud():
                return self.supabase.get_tags()
            return []
    
    def _get_tags_sqlite(self) -> List[Dict[str, Any]]:
        """Get all tags from SQLite."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tags")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_word_tags(self, word_id: int) -> List[Dict[str, Any]]:
        """Get all tags for a word. Always reads from local SQLite."""
        return self._get_word_tags_sqlite(word_id)
    
    def _get_word_tags_sqlite(self, word_id: int) -> List[Dict[str, Any]]:
        """Get all tags for a word from SQLite."""
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT wt.*, t.tag_name 
            FROM word_tags wt 
            JOIN tags t ON wt.tag_id = t.tag_id 
            WHERE wt.word_id = ?
        """, (word_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def _ensure_word_in_cloud(self, word_id: int) -> Optional[int]:
        """Ensure word exists in Supabase and return cloud_id.
        
        Checks if word has cloud_id stored. If not, or if word doesn't exist in Supabase,
        syncs the word first. Returns cloud_id if successful, None otherwise.
        
        Args:
            word_id: Local word ID
            
        Returns:
            Cloud word ID if word exists in Supabase, None otherwise
        """
        if not self._use_cloud():
            return None
        
        # Get word from local DB
        word_data = self._get_word_sqlite(word_id)
        if not word_data:
            logging.warning(f"Word {word_id} not found in local database")
            return None
        
        # Check if word has cloud_id stored
        cloud_id = word_data.get('cloud_id')
        if cloud_id:
            # Verify word exists in Supabase
            cloud_word = self.supabase.get_word(cloud_id)
            if cloud_word:
                return cloud_id
            else:
                # cloud_id exists but word not found in Supabase - might have been deleted
                logging.warning(f"cloud_id {cloud_id} found but word not in Supabase, syncing word {word_id}")
                cloud_id = None
        
        # No cloud_id or word not found in Supabase - sync the word
        if not cloud_id:
            try:
                # Use upsert to sync word to Supabase
                cloud_result = self.supabase.upsert_word(word_data)
                if cloud_result:
                    cloud_id = cloud_result.get('ID') or cloud_result.get('id')
                    if cloud_id:
                        # Store cloud_id for future use
                        self._update_cloud_id_sqlite(word_id, cloud_id)
                        logging.debug(f"Synced word {word_id} to Supabase, cloud_id={cloud_id}")
                        return cloud_id
                    else:
                        logging.warning(f"Synced word {word_id} but couldn't get cloud_id")
                else:
                    logging.warning(f"Failed to sync word {word_id} to Supabase")
            except Exception as e:
                logging.error(f"Error syncing word {word_id} to Supabase: {e}")
        
        return None
    
    def add_tag_to_word(self, word_id: int, tag_name: str) -> bool:
        """Add a tag to a word."""
        # Get or create tag
        tag_id = self._get_or_create_tag_sqlite(tag_name)
        if not tag_id:
            return False
        
        # Add to local
        local_success = self._add_tag_to_word_sqlite(word_id, tag_id)
        
        # Add to cloud if available
        if self._use_cloud() and local_success:
            # Ensure word exists in Supabase and get cloud_id
            cloud_word_id = self._ensure_word_in_cloud(word_id)
            
            if cloud_word_id:
                # Use cloud_id for Supabase tag addition
                cloud_success = self.supabase.add_tag_to_word(cloud_word_id, tag_id)
                if not cloud_success:
                    logging.warning(f"Failed to sync tag addition to cloud, but added locally")
            else:
                # Word not in Supabase, queue tag operation for later sync
                self._queue_operation('INSERT', 'word_tags', word_id, {'tag_id': tag_id})
                logging.warning(f"Word {word_id} not in Supabase, queued tag addition for later sync")
        
        return local_success
    
    def _get_or_create_tag_sqlite(self, tag_name: str) -> Optional[int]:
        """Get tag ID or create if doesn't exist. Also syncs to cloud if available."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        # Check if tag already exists
        cursor.execute("SELECT tag_id FROM tags WHERE tag_name = ?", (tag_name,))
        result = cursor.fetchone()
        
        if result:
            # Tag exists, return its ID
            tag_id = result[0]
            conn.close()
            return tag_id
        
        # Tag doesn't exist, create it
        cursor.execute("INSERT INTO tags (tag_name) VALUES (?)", (tag_name,))
        tag_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Sync new tag to cloud if available
        if self._use_cloud() and tag_id:
            try:
                # Preserve the tag_id when syncing to cloud
                cloud_result = self.supabase.insert_tag(tag_name, tag_id=tag_id)
                if not cloud_result:
                    logging.warning(f"Failed to sync new tag '{tag_name}' to cloud, but created locally")
            except Exception as e:
                logging.warning(f"Error syncing tag '{tag_name}' to cloud: {e}")
        
        return tag_id
    
    def _add_tag_to_word_sqlite(self, word_id: int, tag_id: int) -> bool:
        """Add tag to word in SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO word_tags (word_id, tag_id) VALUES (?, ?)", (word_id, tag_id))
        conn.commit()
        conn.close()
        return True
    
    def remove_tag_from_word(self, word_id: int, tag_name: str) -> bool:
        """Remove a tag from a word."""
        # Get tag ID
        tag_id = self._get_tag_id_sqlite(tag_name)
        if not tag_id:
            return False
        
        # Remove from local
        local_success = self._remove_tag_from_word_sqlite(word_id, tag_id)
        
        # Remove from cloud if available
        if self._use_cloud() and local_success:
            cloud_success = self.supabase.remove_tag_from_word(word_id, tag_id)
            if not cloud_success:
                logging.warning(f"Failed to sync tag removal to cloud, but removed locally")
        
        return local_success
    
    def _get_tag_id_sqlite(self, tag_name: str) -> Optional[int]:
        """Get tag ID by name."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("SELECT tag_id FROM tags WHERE tag_name = ?", (tag_name,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def _remove_tag_from_word_sqlite(self, word_id: int, tag_id: int) -> bool:
        """Remove tag from word in SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM word_tags WHERE word_id = ? AND tag_id = ?", (word_id, tag_id))
        # Check if tag is still used
        cursor.execute("SELECT COUNT(*) FROM word_tags WHERE tag_id = ?", (tag_id,))
        count = cursor.fetchone()[0]
        tag_deleted = False
        if count == 0:
            cursor.execute("DELETE FROM tags WHERE tag_id = ?", (tag_id,))
            tag_deleted = True
        conn.commit()
        conn.close()
        
        # Sync tag deletion to cloud if tag was deleted
        if tag_deleted and self._use_cloud():
            cloud_success = self.supabase.delete_tag(tag_id)
            if not cloud_success:
                logging.warning(f"Failed to sync tag deletion to cloud, but deleted locally")
        
        return True
    
    def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag. Deletes from both local and cloud if available."""
        # Delete from local
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tags WHERE tag_id = ?", (tag_id,))
        conn.commit()
        conn.close()
        
        # Delete from cloud if available
        if self._use_cloud():
            cloud_success = self.supabase.delete_tag(tag_id)
            if not cloud_success:
                logging.warning(f"Failed to sync tag deletion to cloud, but deleted locally")
        
        return True

