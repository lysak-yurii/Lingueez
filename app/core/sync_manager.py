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

from app.core.database_adapter import DatabaseAdapter
from app.core.supabase_client import get_supabase
from app.core.auth_manager import get_auth_manager
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import logging
import os
import threading
import uuid


class SyncError(Exception):
    """A sync that fetched fine but failed to write some/all records locally.
    Distinct from RuntimeError (which the UI treats as a benign shutdown) so a
    real data-integrity failure surfaces instead of masquerading as success."""


class SyncManager:
    """Manages bidirectional sync between local SQLite and Supabase cloud database."""

    def __init__(self):
        # Shared client + auth so a single signed-in token covers sync and CRUD.
        self.supabase = get_supabase()
        self.auth = get_auth_manager()
        from app.core.db import get_active_db_path
        self.local_db = get_active_db_path()
        self.sync_metadata_file = '.last_sync'
        self.cleanup_metadata_file = '.last_cleanup'
        self.db_adapter = DatabaseAdapter(use_cloud=False)  # Use adapter for queue access
        # Load cleanup grace period from settings
        self.cleanup_grace_period_days = self._load_cleanup_grace_period()
        # Generate unique instance ID for this sync manager
        self.instance_id = str(uuid.uuid4())
        # Thread lock for sync operations
        self._sync_lock = threading.Lock()
    
    def _load_cleanup_grace_period(self) -> int:
        """Load cleanup grace period from settings.cfg, default to 30 days."""
        try:
            settings = {}
            if os.path.exists('settings.cfg'):
                with open('settings.cfg', 'r', encoding='utf-8') as f:
                    settings = dict(line.strip().split('=', 1) for line in f if '=' in line)
            
            grace_period = settings.get('cleanup_grace_period_days', '30')
            try:
                return int(grace_period)
            except (ValueError, TypeError):
                return 30
        except Exception as e:
            logging.warning(f"Error loading cleanup grace period from settings: {e}, using default 30 days")
            return 30
    
    def set_local_db(self, path: str):
        """Repoint sync at a different local SQLite file (account switch). Updates
        both this manager and its internal queue-access adapter."""
        self.local_db = path
        self.db_adapter.set_local_db(path)

    def is_sync_enabled(self) -> bool:
        """Sync requires a signed-in account AND a reachable Supabase.

        The auth check is essential: with RLS on, an anonymous connectivity
        probe returns an empty result (no error), so is_connected() alone would
        report True while logged out and we'd "sync" nothing under no identity.
        """
        if not self.auth.is_logged_in():
            return False
        return self.supabase.is_connected()

    # ---- account ownership of the local DB ----------------------------
    def get_synced_account_id(self) -> Optional[str]:
        """The user_id this local dictionary.db was last synced with."""
        return self.db_adapter.get_sync_metadata('synced_account_id')

    def set_synced_account_id(self, uid: Optional[str]):
        self.db_adapter.set_sync_metadata('synced_account_id', uid or '')

    def prepare_account(self, uid: str) -> str:
        """Classify a login against the local DB's recorded owner:

        'first'  — no previous owner (fresh/local-only DB) → safe to upload.
        'same'   — same account as before → normal incremental sync.
        'switch' — a *different* account → caller must archive + reset the local
                   DB before syncing, so two accounts never merge.
        """
        prev = self.get_synced_account_id()
        if not prev:
            return 'first'
        return 'same' if prev == uid else 'switch'

    def archive_and_reset_local_db(self) -> bool:
        """Back up the current dictionary.db (timestamped, into backups/) and
        start a fresh empty one. Used when switching to a different account on a
        machine that already holds someone else's data. Never deletes silently."""
        import shutil
        from app.core.db import initialize_database
        try:
            os.makedirs('backups', exist_ok=True)
            if os.path.exists(self.local_db):
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                archive = os.path.join('backups', f'dictionary_account_switch_{stamp}.db')
                shutil.copy2(self.local_db, archive)
                os.remove(self.local_db)
                logging.info(f"Archived previous account's local DB to {archive}")
            initialize_database()
            return True
        except Exception as exc:
            logging.error(f"Could not archive/reset local DB on account switch: {exc}")
            return False

    def export_user_data(self, path: str) -> Tuple[bool, Optional[str]]:
        """Write the signed-in account's cloud rows to a JSON file (GDPR data
        portability). RLS scopes every select to the current user automatically."""
        if not self.auth.is_logged_in():
            return False, "Sign in first to export your account data."
        client = self.supabase.client
        if client is None:
            return False, "Not connected to the cloud."
        try:
            import json
            payload = {
                'account': self.auth.current_user(),
                'exported_at': datetime.now(timezone.utc).isoformat(),
                'data': {},
            }
            for table in ('words', 'texts', 'tags', 'word_tags'):
                payload['data'][table] = client.table(table).select('*').execute().data
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            return True, None
        except Exception as exc:
            return False, str(exc)

    def delete_account(self) -> Tuple[bool, Optional[str]]:
        """Call the server-side delete_account() RPC (removes the auth user and,
        via on-delete-cascade, every row they own), then sign out locally."""
        if not self.auth.is_logged_in():
            return False, "Sign in first."
        client = self.supabase.client
        if client is None:
            return False, "Not connected to the cloud."
        try:
            client.rpc('delete_account').execute()
        except Exception as exc:
            return False, (f"Could not delete the account ({exc}). Make sure the "
                           f"latest schema (with delete_account()) has been applied.")
        self.auth.sign_out()
        return True, None

    def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status information."""
        status = {
            'enabled': self.is_sync_enabled(),
            'last_sync_time': self._get_last_sync_time(),
            'first_sync_completed': self.db_adapter.get_sync_metadata('first_sync_completed') == 'true',
            'pending_operations': len(self.db_adapter._get_pending_operations()),
            'pending_deletions': len(self.db_adapter._get_pending_deletions())
        }
        return status
    
    def sync_on_startup(self):
        """Sync when app starts. Enhanced sync with deletion tracking and conflict resolution."""
        if not self.auth.is_logged_in():
            logging.info("Not signed in — staying local-only, skipping sync")
            return
        # Refresh the access token first; a stale one would 401 every call below.
        self.auth.refresh_if_needed()
        if not self.is_sync_enabled():
            logging.warning("Supabase not connected, skipping sync")
            return

        # Test actual connectivity before proceeding (not just client existence)
        # CRITICAL: We must verify we can actually reach Supabase, not just that client exists
        # If internet is off, Supabase queries will fail, and we should abort sync
        # 
        # The Supabase client methods catch exceptions and return empty lists, so we need
        # to test connectivity by actually making a network call that will fail if offline
        try:
            # Try to actually query Supabase - the underlying client will raise an exception
            # if there's no internet connection. We need to catch it at the client level.
            # Since get_words() catches exceptions, we need to test the client directly
            if not self.supabase.client:
                logging.warning("Supabase client not initialized - skipping sync")
                return
            
            # Try a direct query that will raise an exception on network error
            # The Supabase Python client raises exceptions for network errors
            try:
                test_response = self.supabase.client.table('words').select('id').limit(1).execute()
                # If we get here, we have connectivity (even if result is empty)
            except Exception as network_error:
                # Check if it's a network-related error
                error_str = str(network_error).lower()
                if any(keyword in error_str for keyword in ['connection', 'network', 'timeout', 'unreachable', 'resolve', 'dns']):
                    logging.warning(f"Cannot reach Supabase (no internet): {network_error} - skipping sync to preserve local data")
                    return
                # If it's a different error (auth, etc), log it but continue
                logging.warning(f"Supabase query error (may be auth/other issue): {network_error}")
        except Exception as connectivity_error:
            # Any other error - be safe and skip sync
            logging.warning(f"Connectivity test failed: {connectivity_error} - skipping sync to preserve local data")
            return
        
        # Acquire sync lock to prevent concurrent syncs
        if not self.db_adapter.acquire_sync_lock(self.instance_id, timeout_seconds=600):
            logging.warning("Sync already in progress, skipping this sync attempt")
            return
        
        sync_successful = False
        try:
            logging.info("Starting enhanced sync on startup...")

            # The active DB file may have been deleted (or recreated empty by a
            # bare sqlite connect) since startup; ensure its tables exist before we
            # read local data, or the initial pull aborts with "no such table".
            from app.core.db import initialize_database
            initialize_database(self.local_db)

            # Check if this is first-time sync
            is_first_sync = self._is_first_sync()
            
            if is_first_sync:
                logging.info("Detected first-time sync, performing full bidirectional sync...")
                self._perform_initial_sync()
                sync_successful = True
            else:
                # Incremental sync
                # 1. Get last sync timestamp
                last_sync = self._get_last_sync_time()
                
                # Safety check: If last_sync is None and local DB is empty, fall back to initial sync
                if last_sync is None:
                    try:
                        conn = sqlite3.connect(self.local_db)
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM words")
                        local_word_count = cursor.fetchone()[0]
                        cursor.execute("SELECT COUNT(*) FROM texts")
                        local_text_count = cursor.fetchone()[0]
                        conn.close()
                        
                        if local_word_count == 0 and local_text_count == 0:
                            # Local DB is empty and no last_sync - treat as first sync
                            logging.warning("Incremental sync detected but local DB is empty and no last_sync - falling back to initial sync")
                            self._perform_initial_sync()
                            return
                    except Exception as e:
                        logging.warning(f"Error checking local DB state during incremental sync: {e}, continuing with incremental sync")
                
                # 2. Pull changes from cloud
                logging.info(f"Starting incremental sync (last_sync: {last_sync or 'None - will fetch all records'})")
                cloud_changes = self._get_cloud_changes_since(last_sync)
                logging.info(f"Found {len(cloud_changes.get('words', []))} word changes, {len(cloud_changes.get('texts', []))} text changes, {len(cloud_changes.get('tags', []))} tag changes from cloud")
                
                # 2a. Get soft deletions from cloud
                cloud_deletions = self._get_cloud_deletions_since(last_sync)
                logging.info(f"Found {len(cloud_deletions.get('words', []))} word deletions and {len(cloud_deletions.get('texts', []))} text deletions from cloud")
                
                # 2b. Detect missing records in cloud (for hard deletes or first sync)
                missing_records = self._detect_missing_records()
                if missing_records:
                    logging.info(f"Detected {len(missing_records.get('words', []))} missing words and {len(missing_records.get('texts', []))} missing texts in cloud")
                
                # 2c. Detect missing records in local (words in cloud but not in local by cloud_id or content)
                # This catches words that might have been missed by timestamp filtering (e.g., due to clock differences)
                missing_in_local = self._detect_missing_in_local()
                if missing_in_local:
                    logging.info(f"Detected {len(missing_in_local.get('words', []))} words in cloud that are missing in local (will be synced)")
                    # Add missing words to cloud_changes so they get synced
                    if 'words' not in cloud_changes:
                        cloud_changes['words'] = []
                    # Avoid duplicates - check if word is already in cloud_changes
                    existing_cloud_ids = {w.get('ID') or w.get('id') for w in cloud_changes.get('words', [])}
                    for word in missing_in_local.get('words', []):
                        cloud_id = word.get('ID') or word.get('id')
                        if cloud_id and cloud_id not in existing_cloud_ids:
                            cloud_changes['words'].append(word)
                            logging.debug(f"Added missing word {cloud_id} to cloud_changes")
                
                # 3. Get pending local deletions
                pending_deletions = self.db_adapter._get_pending_deletions()
                logging.info(f"Found {len(pending_deletions)} pending deletions")
                
                # 4. Get pending local operations
                pending_operations = self.db_adapter._get_pending_operations()
                logging.info(f"Found {len(pending_operations)} pending operations")
                
                # 5. Detect and resolve conflicts before applying changes
                conflicts = self._detect_conflicts(cloud_changes, pending_deletions, pending_operations, cloud_deletions)
                resolved_conflicts = set()
                deletion_conflicts = []
                if conflicts:
                    logging.info(f"Detected {len(conflicts)} conflicts, resolving...")
                    resolved_conflicts, deletion_conflicts = self._resolve_conflicts(conflicts)
                
                # 6. Apply cloud deletions to local (handle conflicts)
                if cloud_deletions or missing_records:
                    self._apply_cloud_deletions_to_local(cloud_deletions, missing_records, pending_deletions, deletion_conflicts, pending_operations)
                
                # 7. Apply cloud changes to local (after conflict resolution)
                if cloud_changes:
                    self._apply_cloud_to_local_with_conflict_check(cloud_changes, pending_deletions, resolved_conflicts)
                
                # 8. Optimize queue: ensure INSERT+DELETE pairs are processed in correct order
                pending_deletions, pending_operations = self._optimize_sync_queue(pending_deletions, pending_operations)
                
                # 9. Sync local operation queue to cloud FIRST (so INSERTs happen before DELETEs)
                # This ensures words added then deleted offline will be uploaded then soft-deleted
                if pending_operations:
                    self._sync_operation_queue(pending_operations)
                
                # 10. Sync local deletions to cloud (after operations, so soft-delete happens after insert)
                if pending_deletions:
                    self._sync_deletions(pending_deletions)
                
                # 9a. Sync tags and word_tags
                self._sync_tags_incremental(last_sync)
                
                # 10. Clean up old synced operations (local)
                self.db_adapter._clear_synced_operations()
                
                # 11. Clean up old soft-deleted records in cloud (periodic cleanup)
                self._cleanup_old_cloud_deletions()
                
                # 12. Update last sync time
                self._update_last_sync_time()
                
                sync_successful = True
                logging.info("Sync completed successfully")
            
            # Mark that sync has been performed at least once (only if successful)
            if sync_successful:
                self.db_adapter.set_sync_metadata('first_sync_completed', 'true')
                # Record which account this local DB now belongs to, so a later
                # login as a different user can detect the mismatch (see
                # prepare_account) instead of cross-contaminating data.
                self.set_synced_account_id(self.auth.current_user_id())

                # Validate sync
                validation_result = self._validate_sync()
                if not validation_result:
                    logging.warning("Sync validation found inconsistencies - sync may be incomplete")
                else:
                    logging.info("Sync validation passed")
            else:
                logging.warning("Sync did not complete successfully - metadata not updated")
            
        except SyncError:
            # Records couldn't be saved locally — a real data-integrity failure.
            # Surface it: metadata stays unstamped and _run_startup_sync reports an
            # error rather than a false "Sync completed".
            logging.error("Sync incomplete — some records failed to save locally",
                          exc_info=True)
            raise
        except Exception as e:
            logging.error(f"Sync failed: {e}", exc_info=True)
            sync_successful = False
            # Local-first: a transient error (e.g. a network drop mid-sync) shouldn't
            # crash the app or alarm the user; the next sync retries.
        finally:
            # Always release sync lock
            self.db_adapter.release_sync_lock()
    
    def quick_pull_words(self):
        """Lightweight pull-only sync: fetch new/updated/deleted words from cloud and apply to local.
        
        This is much faster than full sync and is used before adding words to ensure
        we have the latest words from web. No conflict resolution, no pushing local changes.
        Handles additions, updates, and deletions.
        """
        if not self.is_sync_enabled():
            logging.debug("Supabase not connected, skipping quick pull")
            return False
        
        # Check if lock is held - if so, don't do quick pull (full sync is running)
        if self.db_adapter.is_sync_lock_held():
            logging.debug("Sync lock held, skipping quick pull (full sync in progress)")
            return False
        
        # Try to acquire lock with short timeout (1 second)
        if not self.db_adapter.acquire_sync_lock(self.instance_id, timeout_seconds=1):
            logging.debug("Could not acquire lock for quick pull, skipping")
            return False
        
        try:
            # Get last sync time
            last_sync = self._get_last_sync_time()
            
            # Fetch words changes (new/updated) and deletions from cloud
            try:
                words_changes = self.supabase.get_changes_since('words', last_sync)
                words_deletions = self.supabase.get_soft_deletions_since('words', last_sync)
            except Exception as e:
                logging.warning(f"Error fetching words from cloud during quick pull: {e}")
                return False
            
            # Check if there are any changes at all
            if not words_changes and not words_deletions:
                logging.debug("Quick pull: no changes from cloud")
                return True
            
            # Apply words to local (simple insert/update/delete, no conflict resolution)
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            
            try:
                words_processed = 0
                words_added = 0
                words_updated = 0
                words_deleted = 0
                
                # Process additions/updates
                for word in words_changes:
                    cloud_id = word.get('ID') or word.get('id')
                    language1 = word.get('Language1')
                    word1 = word.get('Word1')
                    language2 = word.get('Language2')
                    word2 = word.get('Word2')
                    
                    if not all([cloud_id, language1, word1, language2, word2]):
                        continue
                    
                    # Check if word exists (by cloud_id or content) to track insert vs update
                    cursor.execute("SELECT ID FROM words WHERE cloud_id = ?", (cloud_id,))
                    exists_by_cloud_id = cursor.fetchone()
                    
                    if not exists_by_cloud_id:
                        cursor.execute("""
                            SELECT ID FROM words 
                            WHERE Language1 = ? AND Word1 = ? AND Language2 = ? AND Word2 = ?
                        """, (language1, word1, language2, word2))
                        exists_by_content = cursor.fetchone()
                    else:
                        exists_by_content = None
                    
                    # Track if this is a new word or update
                    is_new = not (exists_by_cloud_id or exists_by_content)
                    
                    # Sync word to local (handles both insert and update)
                    # For quick pull, we use cloud version (last-write-wins from cloud)
                    self._sync_word_to_local(cursor, word)
                    words_processed += 1
                    
                    if is_new:
                        words_added += 1
                    else:
                        words_updated += 1
                
                # Process deletions
                for deletion in words_deletions:
                    word_id = deletion.get('ID') or deletion.get('id')
                    if word_id:
                        # Delete locally (simple deletion, no conflict checking for quick pull)
                        cursor.execute("DELETE FROM words WHERE ID = ?", (word_id,))
                        words_deleted += 1
                        logging.debug(f"Quick pull: deleted word {word_id} locally (synced from cloud)")
                
                conn.commit()
                
                # Log summary of changes
                if words_processed > 0 or words_deleted > 0:
                    log_parts = []
                    if words_processed > 0:
                        change_details = []
                        if words_added > 0:
                            change_details.append(f"{words_added} new")
                        if words_updated > 0:
                            change_details.append(f"{words_updated} updated")
                        if change_details:
                            log_parts.append(", ".join(change_details))
                    if words_deleted > 0:
                        log_parts.append(f"{words_deleted} deleted")
                    
                    log_msg = f"Quick pull: processed {words_processed + words_deleted} change(s) from cloud"
                    if log_parts:
                        log_msg += f" ({', '.join(log_parts)})"
                    logging.info(log_msg)
                
                return True
            except Exception as e:
                logging.error(f"Error applying changes during quick pull: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()
        finally:
            # Release lock
            self.db_adapter.release_sync_lock()
    
    def _validate_sync(self) -> bool:
        """Validate that sync completed successfully by comparing record counts."""
        try:
            # Get counts from both sides
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM words")
            local_word_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM texts")
            local_text_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM tags")
            local_tag_count = cursor.fetchone()[0]
            
            conn.close()
            
            # Get cloud counts
            cloud_word_ids = self.supabase.get_all_ids('words')
            cloud_text_ids = self.supabase.get_all_ids('texts')
            cloud_tag_ids = self.supabase.get_all_tag_ids()
            
            cloud_word_count = len(cloud_word_ids)
            cloud_text_count = len(cloud_text_ids)
            cloud_tag_count = len(cloud_tag_ids)
            
            # Compare counts (allow small differences due to timing)
            word_diff = abs(local_word_count - cloud_word_count)
            text_diff = abs(local_text_count - cloud_text_count)
            tag_diff = abs(local_tag_count - cloud_tag_count)
            
            # Log differences
            if word_diff > 0:
                logging.warning(f"Word count mismatch: local={local_word_count}, cloud={cloud_word_count}, diff={word_diff}")
            if text_diff > 0:
                logging.warning(f"Text count mismatch: local={local_text_count}, cloud={cloud_text_count}, diff={text_diff}")
            if tag_diff > 0:
                logging.warning(f"Tag count mismatch: local={local_tag_count}, cloud={cloud_tag_count}, diff={tag_diff}")
            
            # Consider validation passed if differences are small (allowing for pending operations)
            # We allow up to 10 records difference to account for pending sync operations
            max_allowed_diff = 10
            validation_passed = (word_diff <= max_allowed_diff and 
                               text_diff <= max_allowed_diff and 
                               tag_diff <= max_allowed_diff)
            
            if validation_passed:
                logging.info(f"Sync validation: words={local_word_count}/{cloud_word_count}, "
                           f"texts={local_text_count}/{cloud_text_count}, "
                           f"tags={local_tag_count}/{cloud_tag_count}")
            
            return validation_passed
            
        except Exception as e:
            logging.error(f"Error during sync validation: {e}")
            return False  # Fail validation on error
    
    def _is_first_sync(self) -> bool:
        """Check if this is the first sync (no previous sync metadata or empty local DB)."""
        # First, check if local database is actually empty (no words/texts)
        # This is the most reliable indicator - if DB is empty, we need to do initial sync
        local_has_data = False
        try:
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM words")
            local_word_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM texts")
            local_text_count = cursor.fetchone()[0]
            conn.close()
            
            local_has_data = (local_word_count > 0 or local_text_count > 0)
            logging.debug(f"Local DB check: {local_word_count} words, {local_text_count} texts")
        except Exception as e:
            logging.warning(f"Error checking local data for first sync detection: {e}")
            # If we can't check, assume empty and do first sync to be safe
        
        # If local DB is empty, check if cloud has data
        # If cloud has data and local is empty, we need to do initial sync regardless of metadata
        if not local_has_data:
            try:
                cloud_word_count = len(self.supabase.get_all_ids('words'))
                cloud_text_count = len(self.supabase.get_all_ids('texts'))
                cloud_has_data = (cloud_word_count > 0 or cloud_text_count > 0)
                logging.debug(f"Cloud DB check: {cloud_word_count} words, {cloud_text_count} texts")
                
                if cloud_has_data:
                    # Local is empty but cloud has data - force first sync
                    logging.info("Local DB is empty but cloud has data - forcing initial sync")
                    return True
            except Exception as e:
                logging.warning(f"Error checking cloud data for first sync detection: {e}")
        
        # If local has data, check metadata to determine if sync has been done before
        # Check if first sync has been completed
        first_sync_completed = self.db_adapter.get_sync_metadata('first_sync_completed')
        if first_sync_completed == 'true':
            # Metadata says sync was completed, but verify last_sync exists too
            last_sync = self._get_last_sync_time()
            if last_sync:
                logging.debug("First sync already completed and last_sync exists - using incremental sync")
                return False
            else:
                # Metadata says completed but no last_sync - treat as first sync
                logging.warning("Metadata says first sync completed but no last_sync found - treating as first sync")
                return True
        
        # Check if last sync time exists
        last_sync = self._get_last_sync_time()
        if last_sync:
            logging.debug(f"Last sync time found: {last_sync} - using incremental sync")
            return False
        
        # No metadata and no last_sync - check if either side has data
        if local_has_data:
            # Local has data, but we haven't synced - this is first sync
            logging.info("Local has data but no sync metadata - treating as first sync")
            return True
        
        # Check if cloud has any data
        try:
            cloud_word_count = len(self.supabase.get_all_ids('words'))
            cloud_text_count = len(self.supabase.get_all_ids('texts'))
            if cloud_word_count > 0 or cloud_text_count > 0:
                # Cloud has data, but we haven't synced - this is first sync
                logging.info("Cloud has data but no sync metadata - treating as first sync")
                return True
        except Exception as e:
            logging.warning(f"Error checking cloud data for first sync detection: {e}")
        
        # Default to first sync if we can't determine (safer to do full sync)
        logging.info("Unable to determine sync state - defaulting to first sync")
        return True
    
    def _get_last_sync_time(self) -> Optional[str]:
        """Get last sync timestamp from local storage."""
        # Try database metadata first
        last_sync = self.db_adapter.get_sync_metadata('last_sync_time')
        if last_sync:
            return last_sync
        
        # Fallback to file-based storage
        try:
            if os.path.exists(self.sync_metadata_file):
                with open(self.sync_metadata_file, 'r') as f:
                    timestamp = f.read().strip()
                    if timestamp:
                        return timestamp
        except Exception as e:
            logging.error(f"Error reading last sync time: {e}")
        return None
    
    def _update_last_sync_time(self):
        """Update last sync timestamp."""
        timestamp = datetime.now(timezone.utc).isoformat()
        # Store in database metadata
        self.db_adapter.set_sync_metadata('last_sync_time', timestamp)
        # Also update file for backward compatibility
        try:
            with open(self.sync_metadata_file, 'w') as f:
                f.write(timestamp)
        except Exception as e:
            logging.warning(f"Error updating last sync time file: {e}")
    
    def _should_run_cleanup(self) -> bool:
        """Check if cleanup should run (once per day)."""
        try:
            if os.path.exists(self.cleanup_metadata_file):
                with open(self.cleanup_metadata_file, 'r') as f:
                    last_cleanup = f.read().strip()
                    if last_cleanup:
                        last_cleanup_dt = datetime.fromisoformat(last_cleanup)
                        # Normalize to naive datetime if needed
                        if last_cleanup_dt.tzinfo is not None:
                            last_cleanup_dt = last_cleanup_dt.astimezone(timezone.utc).replace(tzinfo=None)
                        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                        # Run cleanup if last cleanup was more than 24 hours ago
                        return (now_dt - last_cleanup_dt).total_seconds() > 86400
            return True  # First time, run cleanup
        except Exception as e:
            logging.error(f"Error checking cleanup time: {e}")
            return False  # On error, skip cleanup
    
    def _update_last_cleanup_time(self):
        """Update last cleanup timestamp."""
        try:
            with open(self.cleanup_metadata_file, 'w') as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception as e:
            logging.error(f"Error updating last cleanup time: {e}")
    
    def _cleanup_old_cloud_deletions(self):
        """Clean up old soft-deleted records in Supabase (runs once per day)."""
        if not self._should_run_cleanup():
            logging.debug("Cleanup skipped: Already ran within last 24 hours")
            return
        
        try:
            logging.info("Running cleanup of old soft-deleted records in cloud...")
            
            # Check how many records would be cleaned up (for logging)
            words_count = self.supabase.get_old_soft_deletes_count('words', self.cleanup_grace_period_days)
            texts_count = self.supabase.get_old_soft_deletes_count('texts', self.cleanup_grace_period_days)
            logging.info(f"Found {words_count} words and {texts_count} texts ready for cleanup")
            
            # Clean up words
            words_deleted = self.supabase.cleanup_old_soft_deletes('words', self.cleanup_grace_period_days)
            
            # Clean up texts
            texts_deleted = self.supabase.cleanup_old_soft_deletes('texts', self.cleanup_grace_period_days)
            
            total_deleted = words_deleted + texts_deleted
            
            if total_deleted > 0:
                logging.info(f"Cleanup completed: Permanently deleted {total_deleted} old soft-deleted records from cloud")
            else:
                if words_count > 0 or texts_count > 0:
                    logging.warning(f"Cleanup found {words_count + texts_count} records but deleted 0. Check logs for errors.")
                else:
                    logging.debug("Cleanup completed: No old soft-deleted records to clean up")
            
            # Update last cleanup time
            self._update_last_cleanup_time()
            
        except Exception as e:
            logging.error(f"Error during cloud cleanup: {e}", exc_info=True)
            # Don't fail sync if cleanup fails
    
    def _get_cloud_changes_since(self, timestamp: Optional[str]):
        """Get changes from Supabase since last sync."""
        changes = {}
        
        # Get words changes
        try:
            words_changes = self.supabase.get_changes_since('words', timestamp)
            changes['words'] = words_changes
        except Exception as e:
            logging.error(f"Error fetching words changes from cloud: {e}")
            changes['words'] = []
        
        # Get texts changes
        try:
            texts_changes = self.supabase.get_changes_since('texts', timestamp)
            changes['texts'] = texts_changes
        except Exception as e:
            logging.error(f"Error fetching texts changes from cloud: {e}")
            changes['texts'] = []
        
        # Get tags changes (tags don't have timestamps, so we get all for incremental sync)
        # In practice, we'll sync tags by comparing full sets
        try:
            tags_changes = self.supabase.get_changes_since('tags', timestamp)
            changes['tags'] = tags_changes
        except Exception as e:
            logging.error(f"Error fetching tags changes from cloud: {e}")
            changes['tags'] = []
        
        # Get word_tags changes
        try:
            word_tags_changes = self.supabase.get_changes_since('word_tags', timestamp)
            changes['word_tags'] = word_tags_changes
        except Exception as e:
            logging.error(f"Error fetching word_tags changes from cloud: {e}")
            changes['word_tags'] = []
        
        return changes
    
    def _sync_tags_incremental(self, last_sync: Optional[str]):
        """Sync tags and word_tags incrementally by comparing full sets."""
        try:
            logging.info("Syncing tags and word_tags...")
            
            # Get all tags and word_tags from both sides
            cloud_tags = self.supabase.get_tags()
            cloud_word_tags = self.supabase.get_all_word_tags()
            
            conn = sqlite3.connect(self.local_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get local tags
            cursor.execute("SELECT * FROM tags")
            local_tags = [dict(row) for row in cursor.fetchall()]
            
            # Get local word_tags
            cursor.execute("SELECT * FROM word_tags")
            local_word_tags = [dict(row) for row in cursor.fetchall()]
            
            # Create mappings
            local_tag_by_name = {t.get('tag_name'): t for t in local_tags}
            cloud_tag_by_name = {t.get('tag_name'): t for t in cloud_tags}
            
            local_tag_by_id = {t.get('tag_id'): t for t in local_tags}
            cloud_tag_by_id = {t.get('tag_id'): t for t in cloud_tags}
            
            local_wt_set = {(wt.get('word_id'), wt.get('tag_id')) for wt in local_word_tags}
            cloud_wt_set = {(wt.get('word_id'), wt.get('tag_id')) for wt in cloud_word_tags}
            
            # Sync tags from cloud to local
            for cloud_tag in cloud_tags:
                tag_name = cloud_tag.get('tag_name')
                cloud_tag_id = cloud_tag.get('tag_id')
                
                if tag_name in local_tag_by_name:
                    # Tag exists locally - check if ID matches
                    local_tag = local_tag_by_name[tag_name]
                    local_tag_id = local_tag.get('tag_id')
                    
                    if local_tag_id != cloud_tag_id:
                        # ID mismatch - update local to use cloud ID
                        # First, update all word_tags that reference the old ID
                        cursor.execute("UPDATE word_tags SET tag_id = ? WHERE tag_id = ?", 
                                     (cloud_tag_id, local_tag_id))
                        # Update tag ID
                        cursor.execute("UPDATE tags SET tag_id = ? WHERE tag_id = ?",
                                     (cloud_tag_id, local_tag_id))
                        logging.info(f"Updated tag {tag_name} ID from {local_tag_id} to {cloud_tag_id}")
                else:
                    # New tag from cloud - insert
                    cursor.execute("INSERT OR IGNORE INTO tags (tag_id, tag_name) VALUES (?, ?)",
                                 (cloud_tag_id, tag_name))
                    logging.info(f"Added tag {tag_name} from cloud")
            
            # Sync word_tags from cloud to local
            for cloud_wt in cloud_word_tags:
                word_id = cloud_wt.get('word_id')
                tag_id = cloud_wt.get('tag_id')
                key = (word_id, tag_id)
                
                if key not in local_wt_set:
                    cursor.execute("INSERT OR IGNORE INTO word_tags (word_id, tag_id) VALUES (?, ?)",
                                 (word_id, tag_id))
                    logging.debug(f"Added word_tag relationship: word {word_id}, tag {tag_id}")
            
            # Remove word_tags that exist locally but not in cloud
            for local_wt in local_word_tags:
                word_id = local_wt.get('word_id')
                tag_id = local_wt.get('tag_id')
                key = (word_id, tag_id)
                
                if key not in cloud_wt_set:
                    cursor.execute("DELETE FROM word_tags WHERE word_id = ? AND tag_id = ?",
                                 (word_id, tag_id))
                    logging.debug(f"Removed word_tag relationship: word {word_id}, tag {tag_id}")
            
            conn.commit()
            
            # Push local-only tags to cloud
            for local_tag in local_tags:
                tag_name = local_tag.get('tag_name')
                local_tag_id = local_tag.get('tag_id')
                
                if tag_name not in cloud_tag_by_name:
                    try:
                        result = self.supabase.insert_tag(tag_name, tag_id=local_tag_id)
                        if result:
                            logging.info(f"Pushed tag {tag_name} to cloud")
                    except Exception as e:
                        logging.warning(f"Failed to push tag {tag_name} to cloud: {e}")
            
            # Push local-only word_tags to cloud
            for local_wt in local_word_tags:
                word_id = local_wt.get('word_id')
                tag_id = local_wt.get('tag_id')
                key = (word_id, tag_id)
                
                if key not in cloud_wt_set:
                    try:
                        success = self.supabase.add_tag_to_word(word_id, tag_id)
                        if success:
                            logging.debug(f"Pushed word_tag relationship to cloud: word {word_id}, tag {tag_id}")
                    except Exception as e:
                        logging.warning(f"Failed to push word_tag to cloud: {e}")
            
            conn.close()
            logging.info("Tags and word_tags sync completed")
            
        except Exception as e:
            logging.error(f"Error syncing tags: {e}", exc_info=True)
    
    def _get_cloud_deletions_since(self, timestamp: Optional[str]):
        """Get soft deletions from Supabase since last sync."""
        deletions = {}
        
        # Get words soft deletions
        try:
            words_deletions = self.supabase.get_soft_deletions_since('words', timestamp)
            deletions['words'] = words_deletions
        except Exception as e:
            logging.error(f"Error fetching words deletions from cloud: {e}")
            deletions['words'] = []
        
        # Get texts soft deletions
        try:
            texts_deletions = self.supabase.get_soft_deletions_since('texts', timestamp)
            deletions['texts'] = texts_deletions
        except Exception as e:
            logging.error(f"Error fetching texts deletions from cloud: {e}")
            deletions['texts'] = []
        
        return deletions
    
    def _detect_missing_records(self):
        """Detect records that exist locally but not in cloud (for hard deletes or first sync).
        
        IMPORTANT: If cloud query fails (e.g., no internet), returns empty list to prevent
        incorrectly treating all local records as "missing" and deleting them.
        """
        missing = {'words': [], 'texts': []}
        
        try:
            # Get all local IDs
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            
            # Get local word IDs
            cursor.execute("SELECT ID FROM words")
            local_word_ids = {row[0] for row in cursor.fetchall()}
            
            # Get local text IDs
            cursor.execute("SELECT ID FROM texts")
            local_text_ids = {row[0] for row in cursor.fetchall()}
            
            conn.close()
            
            # Get all cloud IDs (excluding soft-deleted)
            # CRITICAL: If this fails (no internet), we must NOT treat local records as missing
            try:
                cloud_word_ids = set(self.supabase.get_all_ids('words'))
                cloud_text_ids = set(self.supabase.get_all_ids('texts'))
            except Exception as cloud_error:
                # Cloud query failed - likely no internet connection
                # Return empty missing list to prevent deleting local records
                logging.error(f"Failed to query cloud for missing records detection: {cloud_error}")
                logging.warning("Skipping missing records detection - cloud query failed. Local records will be preserved.")
                return missing
            
            # CRITICAL SAFETY CHECK: If cloud returned empty but we have local data,
            # this could indicate a connection failure rather than actual missing records.
            # We should NOT delete local records in this case.
            if len(local_word_ids) > 0 and len(cloud_word_ids) == 0:
                # This could mean:
                # 1. Cloud is actually empty (first sync scenario - but we should upload, not delete)
                # 2. Cloud query failed silently (no internet) - DEFINITELY don't delete
                # 
                # Since we already passed connectivity test at start of sync, if we get here
                # with empty cloud results, it's safer to assume it's a connection issue
                # rather than that all local words should be deleted.
                # 
                # Only treat as "missing" if we're certain cloud is actually empty AND
                # we're doing a first sync (which would upload, not delete).
                # For incremental sync, empty cloud with local data = connection issue, skip.
                logging.warning(f"Cloud returned empty but local has {len(local_word_ids)} words - treating as connection issue, not missing records")
                logging.warning("Skipping missing records detection to preserve local data")
                return missing
            
            # Find missing words
            missing_word_ids = local_word_ids - cloud_word_ids
            for word_id in missing_word_ids:
                missing['words'].append({'ID': word_id})
            
            # Find missing texts
            missing_text_ids = local_text_ids - cloud_text_ids
            for text_id in missing_text_ids:
                missing['texts'].append({'ID': text_id})
            
        except Exception as e:
            logging.error(f"Error detecting missing records: {e}")
            # On any error, return empty to be safe (don't delete local records)
            return missing
        
        return missing
    
    def _detect_missing_in_local(self):
        """Detect records that exist in cloud but not in local (by cloud_id or content).
        
        This catches words that might have been missed by timestamp filtering
        (e.g., due to clock differences or timing issues).
        """
        missing = {'words': [], 'texts': []}
        
        try:
            # Get all cloud words
            cloud_words = self.supabase.get_words()
            if not cloud_words:
                return missing
            
            # Get local words with their cloud_ids and content
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            
            # Get local words with cloud_id
            cursor.execute("SELECT cloud_id FROM words WHERE cloud_id IS NOT NULL")
            local_cloud_ids = {row[0] for row in cursor.fetchall()}
            
            # Get local words by content (for words without cloud_id)
            cursor.execute("SELECT Language1, Word1, Language2, Word2 FROM words")
            local_content_keys = set()
            for row in cursor.fetchall():
                if all(row):  # All fields must be present
                    content_key = (row[0], row[1], row[2], row[3])
                    local_content_keys.add(content_key)
            
            conn.close()
            
            # Check each cloud word
            for cloud_word in cloud_words:
                cloud_id = cloud_word.get('ID') or cloud_word.get('id')
                language1 = cloud_word.get('Language1')
                word1 = cloud_word.get('Word1')
                language2 = cloud_word.get('Language2')
                word2 = cloud_word.get('Word2')
                
                # Check if word exists locally by cloud_id
                found_by_cloud_id = cloud_id and cloud_id in local_cloud_ids
                
                # Check if word exists locally by content
                found_by_content = False
                if all([language1, word1, language2, word2]):
                    content_key = (language1, word1, language2, word2)
                    found_by_content = content_key in local_content_keys
                
                # If not found by either method, it's missing in local
                if not found_by_cloud_id and not found_by_content:
                    missing['words'].append(cloud_word)
                    logging.debug(f"Detected missing word in local: cloud_id={cloud_id}, content=({language1}, {word1}, {language2}, {word2})")
            
            logging.info(f"Detected {len(missing['words'])} words in cloud that are missing in local")
            
        except Exception as e:
            logging.error(f"Error detecting missing records in local: {e}")
            # On any error, return empty to be safe
            return missing
        
        return missing
    
    def _apply_cloud_deletions_to_local(self, cloud_deletions: dict, missing_records: dict, pending_deletions: List[Dict[str, Any]], deletion_conflicts: List[Dict[str, Any]], pending_operations: List[Dict[str, Any]] = None):
        """Apply cloud deletions to local database, handling conflicts.
        
        Args:
            cloud_deletions: Soft deletions from cloud
            missing_records: Records that exist locally but not in cloud
            pending_deletions: Local deletions pending sync
            deletion_conflicts: Conflicts resolved in favor of local
            pending_operations: Operations queued for upload (INSERT/UPDATE) - records in this queue should NOT be deleted
        """
        if pending_operations is None:
            pending_operations = []
        
        # SAFETY CHECK: If missing_records contains many records but cloud_deletions is empty,
        # this might indicate a connection issue rather than actual missing records.
        # Don't delete local records in this case.
        missing_words_count = len(missing_records.get('words', []))
        missing_texts_count = len(missing_records.get('texts', []))
        cloud_deletions_words_count = len(cloud_deletions.get('words', []))
        cloud_deletions_texts_count = len(cloud_deletions.get('texts', []))
        
        if missing_words_count > 0 and cloud_deletions_words_count == 0:
            # Many "missing" words but no cloud deletions - suspicious, might be connection issue
            logging.warning(f"Found {missing_words_count} 'missing' words but no cloud deletions - treating as potential connection issue, skipping deletion")
            missing_records['words'] = []  # Clear to prevent deletion
        
        if missing_texts_count > 0 and cloud_deletions_texts_count == 0:
            # Many "missing" texts but no cloud deletions - suspicious, might be connection issue
            logging.warning(f"Found {missing_texts_count} 'missing' texts but no cloud deletions - treating as potential connection issue, skipping deletion")
            missing_records['texts'] = []  # Clear to prevent deletion
        
        # Combine soft deletions and missing records
        all_deletions = {
            'words': cloud_deletions.get('words', []) + missing_records.get('words', []),
            'texts': cloud_deletions.get('texts', []) + missing_records.get('texts', [])
        }
        
        # Track which deletions to skip due to conflicts
        skip_deletions = {(c['table_name'], c['record_id']) for c in deletion_conflicts}
        
        # Extract record IDs from pending operations (INSERT/UPDATE operations)
        # These records are queued for upload and should NOT be deleted locally
        pending_record_ids = set()
        for operation in pending_operations:
            if operation['operation_type'] in ['INSERT', 'UPDATE']:
                table_name = operation['table_name']
                record_id = operation['record_id']
                pending_record_ids.add((table_name, record_id))
        
        if pending_record_ids:
            logging.debug(f"Found {len(pending_record_ids)} records in pending operations queue - these will be preserved from deletion")
        
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # Process word deletions
            for deletion in all_deletions.get('words', []):
                word_id = deletion.get('ID') or deletion.get('id')
                key = ('words', word_id)
                
                # Skip if conflict resolved in favor of local
                if key in skip_deletions:
                    logging.debug(f"Skipping cloud deletion of word {word_id} - conflict resolved (local wins)")
                    continue
                
                # Check if locally deleted (already handled)
                local_deleted = any(d['table_name'] == 'words' and d['record_id'] == word_id for d in pending_deletions)
                if local_deleted:
                    logging.debug(f"Skipping cloud deletion of word {word_id} - already deleted locally")
                    continue
                
                # Skip if record is in pending operations queue (queued for upload)
                if key in pending_record_ids:
                    logging.info(f"Skipping deletion of word {word_id} - queued for upload to cloud")
                    continue
                
                # Delete locally
                cursor.execute("DELETE FROM words WHERE ID = ?", (word_id,))
                logging.info(f"Deleted word {word_id} locally (synced from cloud)")
            
            # Process text deletions
            for deletion in all_deletions.get('texts', []):
                text_id = deletion.get('ID') or deletion.get('id')
                key = ('texts', text_id)
                
                # Skip if conflict resolved in favor of local
                if key in skip_deletions:
                    logging.debug(f"Skipping cloud deletion of text {text_id} - conflict resolved (local wins)")
                    continue
                
                # Check if locally deleted (already handled)
                local_deleted = any(d['table_name'] == 'texts' and d['record_id'] == text_id for d in pending_deletions)
                if local_deleted:
                    logging.debug(f"Skipping cloud deletion of text {text_id} - already deleted locally")
                    continue
                
                # Skip if record is in pending operations queue (queued for upload)
                if key in pending_record_ids:
                    logging.info(f"Skipping deletion of text {text_id} - queued for upload to cloud")
                    continue
                
                # Delete locally
                cursor.execute("DELETE FROM texts WHERE ID = ?", (text_id,))
                logging.info(f"Deleted text {text_id} locally (synced from cloud)")
            
            conn.commit()
            logging.info("Applied cloud deletions to local database")
            
        except Exception as e:
            logging.error(f"Error applying cloud deletions to local: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _get_local_changes_since(self, timestamp: Optional[str]):
        """Get local SQLite changes since last sync."""
        changes = {}
        
        conn = sqlite3.connect(self.local_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            # Get words changes
            if timestamp:
                cursor.execute("""
                    SELECT * FROM words 
                    WHERE edited_at > ? OR (edited_at IS NULL AND created_at > ?)
                    ORDER BY COALESCE(edited_at, created_at) DESC
                """, (timestamp, timestamp))
            else:
                cursor.execute("SELECT * FROM words ORDER BY COALESCE(edited_at, created_at) DESC")
            
            words_rows = cursor.fetchall()
            changes['words'] = [dict(row) for row in words_rows]
            
            # Get texts changes
            if timestamp:
                cursor.execute("""
                    SELECT * FROM texts 
                    WHERE edited_at > ? OR (edited_at IS NULL AND created_at > ?)
                    ORDER BY COALESCE(edited_at, created_at) DESC
                """, (timestamp, timestamp))
            else:
                cursor.execute("SELECT * FROM texts ORDER BY COALESCE(edited_at, created_at) DESC")
            
            texts_rows = cursor.fetchall()
            changes['texts'] = [dict(row) for row in texts_rows]
            
        except Exception as e:
            logging.error(f"Error fetching local changes: {e}")
            changes['words'] = []
            changes['texts'] = []
        finally:
            conn.close()
        
        return changes
    
    def _apply_cloud_to_local(self, changes: dict):
        """Apply cloud changes to local SQLite."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            # Apply words changes
            for word in changes.get('words', []):
                self._sync_word_to_local(cursor, word)
            
            # Apply texts changes
            for text in changes.get('texts', []):
                self._sync_text_to_local(cursor, text)
            
            conn.commit()
            logging.info("Applied cloud changes to local database")
            
        except Exception as e:
            logging.error(f"Error applying cloud changes to local: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _sync_word_to_local(self, cursor, word_data: dict):
        """Sync a single word from cloud to local.
        
        Matches cloud words to local words by:
        1. cloud_id (if local word already has this cloud_id)
        2. Content (Language1, Word1, Language2, Word2)
        
        If no match found, inserts as new word with auto-generated local ID.
        """
        cloud_id = word_data.get('ID') or word_data.get('id')
        
        if not cloud_id:
            logging.warning("Cannot sync word to local: missing cloud ID")
            return
        
        # Extract content for matching
        language1 = word_data.get('Language1')
        word1 = word_data.get('Word1')
        language2 = word_data.get('Language2')
        word2 = word_data.get('Word2')
        
        if not all([language1, word1, language2, word2]):
            logging.warning(f"Cannot sync word {cloud_id} to local: missing content fields")
            return
        
        # Strategy 1: Try to find by cloud_id (most reliable - word was synced before)
        cursor.execute("SELECT ID, created_at, edited_at FROM words WHERE cloud_id = ?", (cloud_id,))
        existing_by_cloud_id = cursor.fetchone()
        
        # Strategy 2: Try to find by content (word exists locally with same content)
        if not existing_by_cloud_id:
            cursor.execute("""
                SELECT ID, created_at, edited_at FROM words 
                WHERE Language1 = ? AND Word1 = ? AND Language2 = ? AND Word2 = ?
            """, (language1, word1, language2, word2))
            existing_by_content = cursor.fetchone()
        else:
            existing_by_content = None
        
        existing = existing_by_cloud_id or existing_by_content
        
        if existing:
            # Update existing local word
            local_id = existing[0]
            local_created_at = existing[1]
            local_edited_at = existing[2]
            cloud_created_at = word_data.get('created_at')
            cloud_edited_at = word_data.get('edited_at')
            
            # Preserve local created_at if it exists (don't overwrite with cloud's older timestamp)
            # Only use cloud's created_at if local doesn't have one
            final_created_at = local_created_at if local_created_at else cloud_created_at
            
            # Use the newer edited_at timestamp (or cloud's if local doesn't have one)
            final_edited_at = None
            if local_edited_at and cloud_edited_at:
                # Compare timestamps - use the newer one
                comparison = self._compare_timestamps(local_edited_at, cloud_edited_at)
                final_edited_at = local_edited_at if comparison >= 0 else cloud_edited_at
            elif local_edited_at:
                final_edited_at = local_edited_at
            elif cloud_edited_at:
                final_edited_at = cloud_edited_at
            
            # Log if we're preserving local created_at to help debug ordering issues
            if local_created_at and cloud_created_at and local_created_at != cloud_created_at:
                comparison = self._compare_timestamps(local_created_at, cloud_created_at)
                if comparison > 0:
                    logging.debug(f"Preserving local created_at ({local_created_at}) for word {local_id} (cloud: {cloud_created_at}) to maintain display order")
            
            # Update the existing local word
            cursor.execute("""
                UPDATE words SET 
                    RowNumber=?, Source=?, Definition=?, Definition2=?, 
                    Status=?, Language1=?, Word1=?, Language2=?, Word2=?,
                    favorite=?, created_at=?, edited_at=?, cloud_id=?
                WHERE ID=?
            """, (
                word_data.get('RowNumber'),
                word_data.get('Source'),
                word_data.get('Definition'),
                word_data.get('Definition2'),
                word_data.get('Status'),
                word_data.get('Language1'),
                word_data.get('Word1'),
                word_data.get('Language2'),
                word_data.get('Word2'),
                word_data.get('favorite', False),
                final_created_at,  # Preserve local created_at
                final_edited_at,   # Use newer edited_at
                cloud_id,  # Store cloud word ID as cloud_id
                local_id  # Update by local ID, not cloud ID
            ))
            logging.debug(f"Updated local word {local_id} with cloud word {cloud_id}")
        else:
            # Insert new word - let SQLite auto-generate local ID
            cursor.execute("""
                INSERT INTO words (
                    RowNumber, Source, Definition, Definition2, Status,
                    Language1, Word1, Language2, Word2, favorite, created_at, edited_at, cloud_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                word_data.get('RowNumber'),
                word_data.get('Source'),
                word_data.get('Definition'),
                word_data.get('Definition2'),
                word_data.get('Status'),
                word_data.get('Language1'),
                word_data.get('Word1'),
                word_data.get('Language2'),
                word_data.get('Word2'),
                word_data.get('favorite', False),
                word_data.get('created_at'),
                word_data.get('edited_at'),
                cloud_id  # Store cloud word ID as cloud_id
            ))
            new_local_id = cursor.lastrowid
            logging.debug(f"Inserted new local word {new_local_id} from cloud word {cloud_id}")
    
    def _sync_text_to_local(self, cursor, text_data: dict):
        """Sync a single text from cloud to local."""
        text_id = text_data.get('ID') or text_data.get('id')
        
        # Check if exists and get current timestamps
        cursor.execute("SELECT created_at, edited_at FROM texts WHERE ID = ?", (text_id,))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing - preserve local created_at, use newer edited_at
            local_created_at = existing[0]
            local_edited_at = existing[1]
            cloud_created_at = text_data.get('created_at')
            cloud_edited_at = text_data.get('edited_at')
            
            # Preserve local created_at if it exists (don't overwrite with cloud's older timestamp)
            # Only use cloud's created_at if local doesn't have one
            final_created_at = local_created_at if local_created_at else cloud_created_at
            
            # Use the newer edited_at timestamp (or cloud's if local doesn't have one)
            final_edited_at = None
            if local_edited_at and cloud_edited_at:
                # Compare timestamps - use the newer one
                comparison = self._compare_timestamps(local_edited_at, cloud_edited_at)
                final_edited_at = local_edited_at if comparison >= 0 else cloud_edited_at
            elif local_edited_at:
                final_edited_at = local_edited_at
            elif cloud_edited_at:
                final_edited_at = cloud_edited_at
            
            cursor.execute("""
                UPDATE texts SET
                    RowNumber=?, Title=?, Words=?, Text=?, Language=?, Category=?, Level=?,
                    created_at=?, edited_at=?
                WHERE ID=?
            """, (
                text_data.get('RowNumber'),
                text_data.get('Title'),
                text_data.get('Words'),
                text_data.get('Text'),
                text_data.get('Language'),
                text_data.get('Category'),
                text_data.get('Level'),
                final_created_at,  # Preserve local created_at
                final_edited_at,   # Use newer edited_at
                text_id
            ))
        else:
            # Insert new
            cursor.execute("""
                INSERT INTO texts (
                    ID, RowNumber, Title, Words, Text, Language, Category, Level,
                    created_at, edited_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                text_id,
                text_data.get('RowNumber'),
                text_data.get('Title'),
                text_data.get('Words'),
                text_data.get('Text'),
                text_data.get('Language'),
                text_data.get('Category'),
                text_data.get('Level'),
                text_data.get('created_at'),
                text_data.get('edited_at')
            ))
    
    def _apply_local_to_cloud(self, changes: dict):
        """Push local changes to Supabase."""
        try:
            # Push words changes
            for word in changes.get('words', []):
                self._sync_word_to_cloud(word)
            
            # Push texts changes
            for text in changes.get('texts', []):
                self._sync_text_to_cloud(text)
            
            logging.info("Applied local changes to cloud database")
            
        except Exception as e:
            logging.error(f"Error applying local changes to cloud: {e}")
    
    def _sync_word_to_cloud(self, word_data: dict, retry_count: int = 0, max_retries: int = 3):
        """Sync a single word from local to cloud with retry logic.
        
        Uses cloud_id if available for reliable word identification, falls back to content-based matching.
        """
        word_id = word_data.get('ID')
        
        # Prepare data for Supabase (don't include ID - let Supabase auto-generate if inserting)
        supabase_data = {
            'RowNumber': word_data.get('RowNumber'),
            'Source': word_data.get('Source'),
            'Definition': word_data.get('Definition'),
            'Definition2': word_data.get('Definition2'),
            'Status': word_data.get('Status'),
            'Language1': word_data.get('Language1'),
            'Word1': word_data.get('Word1'),
            'Language2': word_data.get('Language2'),
            'Word2': word_data.get('Word2'),
            'favorite': bool(word_data.get('favorite', False)),
            'created_at': word_data.get('created_at'),
            'edited_at': word_data.get('edited_at') or word_data.get('created_at')
        }
        
        # Extract content fields for matching (fallback)
        language1 = word_data.get('Language1')
        word1 = word_data.get('Word1')
        language2 = word_data.get('Language2')
        word2 = word_data.get('Word2')
        
        if not all([language1, word1, language2, word2]):
            logging.error(f"Cannot sync word {word_id}: missing required content fields")
            return
        
        # Check for cloud_id first (most reliable)
        cloud_id = word_data.get('cloud_id')
        
        try:
            result = None
            if cloud_id:
                # Try to update using cloud_id directly
                logging.debug(f"Syncing word {word_id} to cloud using cloud_id {cloud_id}")
                result = self.supabase.update_word(cloud_id, supabase_data)
                if result:
                    # Update succeeded, ensure cloud_id is stored (should already be, but verify)
                    logging.debug(f"Successfully synced word {word_id} to cloud using cloud_id {cloud_id}")
                else:
                    # Update failed - cloud word might have been deleted, clear cloud_id and try upsert
                    logging.warning(f"Update failed for word {word_id} with cloud_id {cloud_id}, trying upsert")
                    cloud_id = None  # Clear to force upsert
            
            # If cloud_id update failed or doesn't exist, use upsert logic
            if not result:
                # Use upsert logic: find by content, update if found, insert if not (auto-generate ID)
                logging.debug(f"Syncing word {word_id} to cloud using content-based matching")
                result = self.supabase.upsert_word(supabase_data)
            
            if result:
                # Store cloud_id from result if we don't have it or it changed
                result_cloud_id = result.get('ID') or result.get('id')
                if result_cloud_id:
                    if not cloud_id or cloud_id != result_cloud_id:
                        # Update cloud_id in local database
                        self.db_adapter._update_cloud_id_sqlite(word_id, result_cloud_id)
                        logging.debug(f"Stored cloud_id={result_cloud_id} for word {word_id}")
                return
            elif retry_count < max_retries:
                logging.warning(f"Retrying sync for word {word_id} (attempt {retry_count + 1}/{max_retries})")
                return self._sync_word_to_cloud(word_data, retry_count + 1, max_retries)
        except Exception as e:
            if retry_count < max_retries:
                logging.warning(f"Retrying sync for word {word_id} after error: {e} (attempt {retry_count + 1}/{max_retries})")
                return self._sync_word_to_cloud(word_data, retry_count + 1, max_retries)
            else:
                logging.error(f"Failed to sync word {word_id} to cloud after {max_retries} retries: {e}")
                # Queue for later sync
                self.db_adapter._queue_operation('UPDATE', 'words', word_id, word_data)
    
    def _sync_text_to_cloud(self, text_data: dict, retry_count: int = 0, max_retries: int = 3):
        """Sync a single text from local to cloud with retry logic."""
        text_id = text_data.get('ID')
        
        # Prepare data for Supabase
        supabase_data = {
            'ID': text_id,
            'RowNumber': text_data.get('RowNumber'),
            'Title': text_data.get('Title'),
            'Words': text_data.get('Words'),
            'Text': text_data.get('Text'),
            'Language': text_data.get('Language'),
            'Category': text_data.get('Category'),
            'Level': text_data.get('Level'),
            'created_at': text_data.get('created_at'),
            'edited_at': text_data.get('edited_at') or text_data.get('created_at')
        }
        
        # Try update first, if fails, insert
        try:
            result = self.supabase.update_text(text_id, supabase_data)
            if not result:
                # Text doesn't exist in cloud, insert it
                result = self.supabase.insert_text(supabase_data, preserve_id=True)
                if not result and retry_count < max_retries:
                    logging.warning(f"Retrying sync for text {text_id} (attempt {retry_count + 1}/{max_retries})")
                    return self._sync_text_to_cloud(text_data, retry_count + 1, max_retries)
        except Exception as e:
            # If update fails, try insert
            try:
                result = self.supabase.insert_text(supabase_data, preserve_id=True)
                if not result and retry_count < max_retries:
                    logging.warning(f"Retrying sync for text {text_id} (attempt {retry_count + 1}/{max_retries})")
                    return self._sync_text_to_cloud(text_data, retry_count + 1, max_retries)
            except Exception as insert_error:
                if retry_count < max_retries:
                    logging.warning(f"Retrying sync for text {text_id} after error: {insert_error} (attempt {retry_count + 1}/{max_retries})")
                    return self._sync_text_to_cloud(text_data, retry_count + 1, max_retries)
                else:
                    logging.error(f"Failed to sync text {text_id} to cloud after {max_retries} retries: {insert_error}")
                    # Queue for later sync
                    self.db_adapter._queue_operation('UPDATE', 'texts', text_id, text_data)
    
    def _optimize_sync_queue(self, pending_deletions: List[Dict[str, Any]], pending_operations: List[Dict[str, Any]]) -> tuple:
        """Optimize sync queue: ensure INSERT+DELETE pairs are processed correctly.
        
        If a record has both an INSERT and DELETE operation:
        - Keep both operations
        - Ensure INSERT happens first, then DELETE (soft delete)
        - This way the record exists in cloud with deleted_at set (appears in bin)
        
        Returns:
            tuple: (optimized_deletions, optimized_operations)
        """
        # Build sets of record IDs that have INSERT operations
        insert_records = set()
        insert_operations = {}
        for operation in pending_operations:
            if operation['operation_type'] == 'INSERT':
                key = (operation['table_name'], operation['record_id'])
                insert_records.add(key)
                insert_operations[key] = operation
        
        # For records with both INSERT and DELETE, ensure proper ordering
        # We'll process INSERTs first, then DELETEs, so the record gets soft-deleted in cloud
        optimized_deletions = []
        optimized_operations = []
        
        # Track which INSERTs have corresponding DELETEs
        inserts_with_deletes = set()
        
        for deletion in pending_deletions:
            key = (deletion['table_name'], deletion['record_id'])
            if key in insert_records:
                # This record has both INSERT and DELETE
                # We want to keep both - INSERT first, then DELETE (soft delete)
                inserts_with_deletes.add(key)
                logging.info(f"Record {deletion['table_name']} {deletion['record_id']} has both INSERT and DELETE - will upload then soft-delete")
            optimized_deletions.append(deletion)
        
        # Reorder operations: INSERTs with DELETEs first, then other INSERTs, then UPDATEs
        insert_ops_with_deletes = []
        insert_ops_without_deletes = []
        update_ops = []
        
        for operation in pending_operations:
            if operation['operation_type'] == 'INSERT':
                key = (operation['table_name'], operation['record_id'])
                if key in inserts_with_deletes:
                    insert_ops_with_deletes.append(operation)
                else:
                    insert_ops_without_deletes.append(operation)
            else:
                update_ops.append(operation)
        
        # Order: INSERTs with DELETEs first, then other INSERTs, then UPDATEs
        optimized_operations = insert_ops_with_deletes + insert_ops_without_deletes + update_ops
        
        return optimized_deletions, optimized_operations
    
    def _sync_deletions(self, deletions: List[Dict[str, Any]]):
        """Sync pending deletions to cloud.
        
        Uses cloud_id if available for reliable word identification, falls back to content-based matching.
        """
        synced_count = 0
        failed_count = 0
        
        for deletion in deletions:
            table_name = deletion['table_name']
            record_id = deletion['record_id']
            
            try:
                if table_name == 'words':
                    # Try to get word data from local database for cloud_id or content-based lookup
                    word_data = self.db_adapter.get_word(record_id)
                    success = False
                    
                    if word_data:
                        # First try to use cloud_id (most reliable)
                        cloud_id = word_data.get('cloud_id')
                        
                        if cloud_id:
                            # Use cloud_id directly
                            success = self.supabase.delete_word(cloud_id)
                            if success:
                                logging.debug(f"Deleted word {record_id} from cloud using cloud_id {cloud_id}")
                            else:
                                logging.warning(f"Failed to delete word {record_id} from cloud using cloud_id {cloud_id}")
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
                                        success = self.supabase.delete_word(cloud_id)
                                        if success:
                                            logging.debug(f"Deleted word {record_id} from cloud using content-based lookup (ID: {cloud_id})")
                                    else:
                                        logging.warning(f"Found cloud word but couldn't get ID for deletion {record_id}")
                                else:
                                    # Word not found in cloud, consider it already deleted
                                    logging.debug(f"Word {record_id} not found in cloud, considering deletion synced")
                                    success = True
                            else:
                                logging.warning(f"Cannot find cloud word by content (missing fields) for deletion {record_id}, trying by ID")
                                # Fallback to ID-based deletion
                                success = self.supabase.delete_word(record_id)
                    else:
                        # Word not found locally (already deleted), try by ID as fallback
                        logging.debug(f"Word {record_id} not found locally, trying deletion by ID")
                        success = self.supabase.delete_word(record_id)
                    
                elif table_name == 'texts':
                    success = self.supabase.delete_text(record_id)
                else:
                    logging.warning(f"Unknown table for deletion: {table_name}")
                    success = False
                
                if success:
                    self.db_adapter._mark_deletion_synced(table_name, record_id)
                    synced_count += 1
                else:
                    failed_count += 1
                    logging.warning(f"Failed to sync deletion of {table_name} record {record_id}")
            except Exception as e:
                failed_count += 1
                logging.error(f"Error syncing deletion of {table_name} record {record_id}: {e}")
        
        logging.info(f"Synced {synced_count} deletions, {failed_count} failed")
    
    def _sync_operation_queue(self, operations: List[Dict[str, Any]]):
        """Sync pending operations from queue to cloud.
        
        Uses cloud_id if available for reliable word identification, falls back to content-based matching.
        """
        synced_count = 0
        failed_count = 0
        
        for operation in operations:
            queue_id = operation['id']
            op_type = operation['operation_type']
            table_name = operation['table_name']
            record_id = operation['record_id']
            op_data = operation.get('operation_data')
            
            try:
                success = False
                
                if table_name == 'words':
                    if op_type == 'INSERT':
                        if op_data:
                            # Use upsert: find by content, update if found, insert if not (auto-generate ID)
                            result = self.supabase.upsert_word(op_data)
                            if result:
                                success = True
                                # Store cloud_id from result
                                cloud_id = result.get('ID') or result.get('id')
                                if cloud_id:
                                    self.db_adapter._update_cloud_id_sqlite(record_id, cloud_id)
                    elif op_type == 'UPDATE':
                        if op_data:
                            # For UPDATE operations, op_data contains the NEW word data
                            # We need to find the cloud word, but content may have changed
                            # Strategy: Try cloud_id first, then content-based matching
                            success = False
                            
                            # Strategy 1: Try to use cloud_id (most reliable)
                            try:
                                local_word = self.db_adapter.get_word(record_id)
                                if local_word:
                                    cloud_id = local_word.get('cloud_id')
                                    if cloud_id:
                                        success = self.supabase.update_word(cloud_id, op_data) is not None
                                        if success:
                                            logging.debug(f"Updated word {record_id} in cloud using cloud_id {cloud_id}")
                            except Exception as e:
                                logging.debug(f"Could not get local word {record_id} for cloud_id lookup: {e}")
                            
                            # Strategy 2: Try to find by new content (in case cloud was already updated)
                            if not success:
                                language1 = op_data.get('Language1') or op_data.get('language1')
                                word1 = op_data.get('Word1') or op_data.get('word1')
                                language2 = op_data.get('Language2') or op_data.get('language2')
                                word2 = op_data.get('Word2') or op_data.get('word2')
                                
                                if all([language1, word1, language2, word2]):
                                    cloud_word = self.supabase.find_word_by_content(language1, word1, language2, word2)
                                    if cloud_word:
                                        cloud_id = cloud_word.get('ID') or cloud_word.get('id')
                                        if cloud_id:
                                            success = self.supabase.update_word(cloud_id, op_data) is not None
                                            if success:
                                                # Store cloud_id for future updates
                                                self.db_adapter._update_cloud_id_sqlite(record_id, cloud_id)
                                                logging.debug(f"Updated word {record_id} in cloud by new content (ID: {cloud_id})")
                            
                            # Strategy 3: If not found by new content, try to get word from local DB
                            # and use old content to find cloud word
                            if not success:
                                try:
                                    local_word = self.db_adapter.get_word(record_id)
                                    if local_word:
                                        # Check if local word content differs from op_data (content changed)
                                        local_lang1 = local_word.get('Language1')
                                        local_w1 = local_word.get('Word1')
                                        local_lang2 = local_word.get('Language2')
                                        local_w2 = local_word.get('Word2')
                                        
                                        language1 = op_data.get('Language1') or op_data.get('language1')
                                        word1 = op_data.get('Word1') or op_data.get('word1')
                                        language2 = op_data.get('Language2') or op_data.get('language2')
                                        word2 = op_data.get('Word2') or op_data.get('word2')
                                        
                                        # If content is different, try to find by old content
                                        if (local_lang1 != language1 or local_w1 != word1 or 
                                            local_lang2 != language2 or local_w2 != word2):
                                            if all([local_lang1, local_w1, local_lang2, local_w2]):
                                                cloud_word = self.supabase.find_word_by_content(local_lang1, local_w1, local_lang2, local_w2)
                                                if cloud_word:
                                                    cloud_id = cloud_word.get('ID') or cloud_word.get('id')
                                                    if cloud_id:
                                                        success = self.supabase.update_word(cloud_id, op_data) is not None
                                                        if success:
                                                            # Store cloud_id for future updates
                                                            self.db_adapter._update_cloud_id_sqlite(record_id, cloud_id)
                                                            logging.debug(f"Updated word {record_id} in cloud by old content (ID: {cloud_id})")
                                except Exception as e:
                                    logging.debug(f"Could not get local word {record_id} for update: {e}")
                            
                            # Strategy 4: Fallback to ID-based update (if IDs happen to match)
                            if not success:
                                try:
                                    success = self.supabase.update_word(record_id, op_data) is not None
                                    if success:
                                        logging.debug(f"Updated word {record_id} in cloud by ID")
                                except Exception as e:
                                    logging.debug(f"ID-based update failed for {record_id}: {e}")
                            
                            # Strategy 5: Last resort - use upsert (will insert if new, update if found by content)
                            if not success:
                                try:
                                    result = self.supabase.upsert_word(op_data)
                                    if result:
                                        success = True
                                        # Store cloud_id from result
                                        cloud_id = result.get('ID') or result.get('id')
                                        if cloud_id:
                                            self.db_adapter._update_cloud_id_sqlite(record_id, cloud_id)
                                        logging.debug(f"Upserted word {record_id} in cloud")
                                except Exception as e:
                                    logging.warning(f"Upsert failed for word {record_id}: {e}")
                
                elif table_name == 'texts':
                    if op_type == 'INSERT':
                        if op_data:
                            success = self.supabase.insert_text(op_data, preserve_id=True) is not None
                    elif op_type == 'UPDATE':
                        if op_data:
                            success = self.supabase.update_text(record_id, op_data) is not None
                
                if success:
                    self.db_adapter._mark_operation_synced(queue_id)
                    synced_count += 1
                else:
                    failed_count += 1
                    logging.warning(f"Failed to sync {op_type} operation for {table_name} record {record_id}")
            except Exception as e:
                failed_count += 1
                logging.error(f"Error syncing {op_type} operation for {table_name} record {record_id}: {e}")
        
        logging.info(f"Synced {synced_count} operations, {failed_count} failed")
    
    def _detect_conflicts(self, cloud_changes: dict, pending_deletions: List[Dict[str, Any]], pending_operations: List[Dict[str, Any]], cloud_deletions: dict = None) -> List[Dict[str, Any]]:
        """Detect conflicts between cloud changes and local changes."""
        if cloud_deletions is None:
            cloud_deletions = {}
        conflicts = []
        
        # Check for conflicts: local deletion vs cloud update
        deletion_ids = {}
        for deletion in pending_deletions:
            key = (deletion['table_name'], deletion['record_id'])
            deletion_ids[key] = deletion
        
        # Check cloud words against local deletions
        for word in cloud_changes.get('words', []):
            word_id = word.get('ID') or word.get('id')
            key = ('words', word_id)
            if key in deletion_ids:
                conflicts.append({
                    'type': 'delete_vs_update',
                    'table_name': 'words',
                    'record_id': word_id,
                    'cloud_data': word,
                    'local_action': 'delete'
                })
        
        # Check cloud texts against local deletions
        for text in cloud_changes.get('texts', []):
            text_id = text.get('ID') or text.get('id')
            key = ('texts', text_id)
            if key in deletion_ids:
                conflicts.append({
                    'type': 'delete_vs_update',
                    'table_name': 'texts',
                    'record_id': text_id,
                    'cloud_data': text,
                    'local_action': 'delete'
                })
        
        # Check for conflicts: both sides modified (compare timestamps)
        operation_ids = {}
        for operation in pending_operations:
            if operation['operation_type'] == 'UPDATE':
                key = (operation['table_name'], operation['record_id'])
                if key not in operation_ids:
                    operation_ids[key] = []
                operation_ids[key].append(operation)
        
        # Check cloud updates against local updates
        for word in cloud_changes.get('words', []):
            word_id = word.get('ID') or word.get('id')
            key = ('words', word_id)
            if key in operation_ids:
                cloud_edited = word.get('edited_at') or word.get('created_at')
                local_ops = operation_ids[key]
                for local_op in local_ops:
                    local_data = local_op.get('operation_data', {})
                    local_edited = local_data.get('edited_at') or local_data.get('created_at')
                    if cloud_edited and local_edited:
                        conflicts.append({
                            'type': 'both_modified',
                            'table_name': 'words',
                            'record_id': word_id,
                            'cloud_data': word,
                            'local_data': local_data,
                            'cloud_timestamp': cloud_edited,
                            'local_timestamp': local_edited
                        })
        
        for text in cloud_changes.get('texts', []):
            text_id = text.get('ID') or text.get('id')
            key = ('texts', text_id)
            if key in operation_ids:
                cloud_edited = text.get('edited_at') or text.get('created_at')
                local_ops = operation_ids[key]
                for local_op in local_ops:
                    local_data = local_op.get('operation_data', {})
                    local_edited = local_data.get('edited_at') or local_data.get('created_at')
                    if cloud_edited and local_edited:
                        conflicts.append({
                            'type': 'both_modified',
                            'table_name': 'texts',
                            'record_id': text_id,
                            'cloud_data': text,
                            'local_data': local_data,
                            'cloud_timestamp': cloud_edited,
                            'local_timestamp': local_edited
                        })
        
        # Check for deletion conflicts: cloud deleted but local modified
        cloud_deletion_ids = {}
        for deletion in cloud_deletions.get('words', []):
            word_id = deletion.get('ID') or deletion.get('id')
            cloud_deletion_ids[('words', word_id)] = deletion
        
        for deletion in cloud_deletions.get('texts', []):
            text_id = deletion.get('ID') or deletion.get('id')
            cloud_deletion_ids[('texts', text_id)] = deletion
        
        # Check if any pending local operations conflict with cloud deletions
        for operation in pending_operations:
            if operation['operation_type'] in ['INSERT', 'UPDATE']:
                key = (operation['table_name'], operation['record_id'])
                if key in cloud_deletion_ids:
                    conflicts.append({
                        'type': 'delete_vs_modify',
                        'table_name': operation['table_name'],
                        'record_id': operation['record_id'],
                        'cloud_action': 'delete',
                        'local_action': operation['operation_type'],
                        'local_data': operation.get('operation_data')
                    })
        
        return conflicts
    
    def _resolve_conflicts(self, conflicts: List[Dict[str, Any]]) -> tuple:
        """Resolve conflicts using conflict resolution strategy. 
        Returns tuple of (set of (table_name, record_id) to skip, list of deletion conflicts)."""
        resolved_to_skip = set()
        deletion_conflicts = []
        
        for conflict in conflicts:
            conflict_type = conflict['type']
            table_name = conflict['table_name']
            record_id = conflict['record_id']
            key = (table_name, record_id)
            
            if conflict_type == 'delete_vs_update':
                # Deletion wins: skip cloud update, keep local deletion
                logging.info(f"Conflict resolved: Local deletion of {table_name} {record_id} wins over cloud update")
                resolved_to_skip.add(key)
                # Local deletion will be synced to cloud
            
            elif conflict_type == 'both_modified':
                # Last-write-wins: compare timestamps using improved comparison
                cloud_ts = conflict.get('cloud_timestamp')
                local_ts = conflict.get('local_timestamp')
                
                if cloud_ts and local_ts:
                    # Use the improved timestamp comparison method
                    comparison = self._compare_timestamps(cloud_ts, local_ts)
                    
                    if comparison > 0:
                        # Cloud is newer, keep cloud version
                        logging.info(f"Conflict resolved: Cloud version of {table_name} {record_id} wins (newer timestamp: {cloud_ts} > {local_ts})")
                        self._remove_operation_from_queue(table_name, record_id)
                        # Cloud version will be applied
                    elif comparison < 0:
                        # Local is newer, keep local version
                        logging.info(f"Conflict resolved: Local version of {table_name} {record_id} wins (newer timestamp: {local_ts} > {cloud_ts})")
                        resolved_to_skip.add(key)
                    else:
                        # Timestamps are equal - use local version as default
                        logging.info(f"Conflict resolved: Timestamps equal for {table_name} {record_id}, keeping local version")
                        resolved_to_skip.add(key)
                elif cloud_ts:
                    # Only cloud has timestamp - prefer cloud
                    logging.info(f"Conflict resolved: Cloud version of {table_name} {record_id} wins (local has no timestamp)")
                    self._remove_operation_from_queue(table_name, record_id)
                elif local_ts:
                    # Only local has timestamp - prefer local
                    logging.info(f"Conflict resolved: Local version of {table_name} {record_id} wins (cloud has no timestamp)")
                    resolved_to_skip.add(key)
                else:
                    # Neither has timestamp - prefer local version
                    logging.info(f"Conflict resolved: Local version of {table_name} {record_id} wins (no timestamps available)")
                    resolved_to_skip.add(key)
            
            elif conflict_type == 'delete_vs_modify':
                # Cloud deleted but local modified: ask user or use strategy
                # For now, use "local wins" strategy (keep local modification, don't delete)
                table_name = conflict['table_name']
                record_id = conflict['record_id']
                key = (table_name, record_id)
                
                logging.info(f"Conflict: Cloud deleted {table_name} {record_id} but local has modifications. Keeping local version.")
                deletion_conflicts.append({
                    'table_name': table_name,
                    'record_id': record_id,
                    'resolution': 'local_wins'
                })
                # Don't apply cloud deletion
                resolved_to_skip.add(key)
        
        return resolved_to_skip, deletion_conflicts
    
    def _remove_operation_from_queue(self, table_name: str, record_id: int):
        """Remove an operation from the sync queue."""
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                DELETE FROM sync_queue
                WHERE table_name = ? AND record_id = ? AND synced_at IS NULL
            ''', (table_name, record_id))
            conn.commit()
        except Exception as e:
            logging.error(f"Error removing operation from queue: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _perform_initial_sync(self):
        """Perform full bidirectional sync for first-time sync scenarios."""
        logging.info("=" * 60)
        logging.info("Performing initial full sync...")
        logging.info("=" * 60)
        
        try:
            # Get all data from both sides
            logging.info("Step 1: Fetching all data from cloud...")
            all_cloud_words = self.supabase.get_words()
            all_cloud_texts = self.supabase.get_texts()
            all_cloud_tags = self.supabase.get_tags()
            all_cloud_word_tags = self.supabase.get_all_word_tags()
            
            logging.info(f"Cloud data fetched: {len(all_cloud_words)} words, {len(all_cloud_texts)} texts, {len(all_cloud_tags)} tags, {len(all_cloud_word_tags)} word_tags")
            
            # Get all local data
            logging.info("Step 2: Fetching all data from local database...")
            try:
                conn = sqlite3.connect(self.local_db)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("SELECT * FROM words")
                all_local_words = [dict(row) for row in cursor.fetchall()]
                
                cursor.execute("SELECT * FROM texts")
                all_local_texts = [dict(row) for row in cursor.fetchall()]
                
                cursor.execute("SELECT * FROM tags")
                all_local_tags = [dict(row) for row in cursor.fetchall()]
                
                cursor.execute("SELECT * FROM word_tags")
                all_local_word_tags = [dict(row) for row in cursor.fetchall()]
                
                conn.close()
                
                logging.info(f"Local data fetched: {len(all_local_words)} words, {len(all_local_texts)} texts, {len(all_local_tags)} tags, {len(all_local_word_tags)} word_tags")
            except sqlite3.OperationalError as e:
                if "no such table" in str(e).lower():
                    logging.error(f"Database tables do not exist: {e}")
                    logging.info("Attempting to initialize database...")
                    # Database should be initialized by the app, but log the error
                    raise Exception(f"Database tables not initialized. Please ensure the database is properly set up: {e}")
                raise
            
            # Determine sync strategy based on which side has data
            local_has_data = len(all_local_words) > 0 or len(all_local_texts) > 0
            cloud_has_data = len(all_cloud_words) > 0 or len(all_cloud_texts) > 0
            
            logging.info(f"Step 3: Determining sync strategy...")
            logging.info(f"  - Local has data: {local_has_data} ({len(all_local_words)} words, {len(all_local_texts)} texts)")
            logging.info(f"  - Cloud has data: {cloud_has_data} ({len(all_cloud_words)} words, {len(all_cloud_texts)} texts)")
            
            if not local_has_data and not cloud_has_data:
                logging.info("Both databases are empty, nothing to sync")
                self._update_last_sync_time()
                return
            
            if local_has_data and not cloud_has_data:
                logging.info("Step 4: Strategy selected - PUSH (local -> cloud)")
                logging.info("Local has data, cloud is empty - pushing all local data to cloud")
                self._push_all_local_to_cloud(all_local_words, all_local_texts, all_local_tags, all_local_word_tags)
            elif cloud_has_data and not local_has_data:
                logging.info("Step 4: Strategy selected - PULL (cloud -> local)")
                logging.info("Cloud has data, local is empty - pulling all cloud data to local")
                self._pull_all_cloud_to_local(all_cloud_words, all_cloud_texts, all_cloud_tags, all_cloud_word_tags)
            else:
                # Both have data - merge them
                logging.info("Step 4: Strategy selected - MERGE (bidirectional)")
                logging.info("Both databases have data - performing merge sync")
                self._merge_sync(all_local_words, all_local_texts, all_local_tags, all_local_word_tags,
                               all_cloud_words, all_cloud_texts, all_cloud_tags, all_cloud_word_tags)
            
            # Update sync metadata
            logging.info("Step 5: Updating sync metadata...")
            self._update_last_sync_time()
            
            # Final validation
            logging.info("Step 6: Validating sync results...")
            try:
                conn = sqlite3.connect(self.local_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM words")
                final_word_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM texts")
                final_text_count = cursor.fetchone()[0]
                conn.close()
                
                logging.info(f"Final local counts: {final_word_count} words, {final_text_count} texts")
                
                # Verify cloud counts match expectations
                cloud_word_count = len(all_cloud_words)
                cloud_text_count = len(all_cloud_texts)
                
                if cloud_has_data and not local_has_data:
                    # After pull, local should have cloud's data
                    if final_word_count != cloud_word_count or final_text_count != cloud_text_count:
                        logging.warning(f"Count mismatch after pull: local ({final_word_count} words, {final_text_count} texts) vs cloud ({cloud_word_count} words, {cloud_text_count} texts)")
                    else:
                        logging.info("✓ Sync validation passed: local counts match cloud counts")
            except Exception as e:
                logging.warning(f"Error during final validation: {e}")
            
            logging.info("=" * 60)
            logging.info("Initial sync completed successfully")
            logging.info("=" * 60)
            
        except Exception as e:
            logging.error("=" * 60)
            logging.error(f"Initial sync failed: {e}", exc_info=True)
            logging.error("=" * 60)
            raise
    
    def _push_all_local_to_cloud(self, words, texts, tags, word_tags):
        """Push all local data to cloud."""
        logging.info("Pushing local data to cloud...")
        
        # Push tags first (no dependencies)
        tag_id_mapping = {}  # Map local tag_id to cloud tag_id
        for tag in tags:
            tag_id = tag.get('tag_id')
            tag_name = tag.get('tag_name')
            if tag_name:
                try:
                    result = self.supabase.insert_tag(tag_name, tag_id=tag_id)
                    if result:
                        cloud_tag_id = result.get('tag_id')
                        if tag_id and cloud_tag_id:
                            tag_id_mapping[tag_id] = cloud_tag_id
                except Exception as e:
                    logging.warning(f"Failed to push tag {tag_name}: {e}")
        
        # Push words (use upsert to handle existing words by content)
        for word in words:
            try:
                self.supabase.upsert_word(word)
            except Exception as e:
                logging.warning(f"Failed to push word {word.get('ID')}: {e}")
        
        # Push texts
        for text in texts:
            try:
                self.supabase.insert_text(text, preserve_id=True)
            except Exception as e:
                logging.warning(f"Failed to push text {text.get('ID')}: {e}")
        
        # Push word_tags (update tag_ids using mapping)
        for word_tag in word_tags:
            word_id = word_tag.get('word_id')
            local_tag_id = word_tag.get('tag_id')
            cloud_tag_id = tag_id_mapping.get(local_tag_id, local_tag_id)
            
            if word_id and cloud_tag_id:
                try:
                    self.supabase.add_tag_to_word(word_id, cloud_tag_id)
                except Exception as e:
                    logging.warning(f"Failed to push word_tag relationship: {e}")
        
        logging.info("Finished pushing local data to cloud")
    
    def _pull_all_cloud_to_local(self, words, texts, tags, word_tags):
        """Pull all cloud data to local."""
        logging.info(f"Pulling cloud data to local: {len(words)} words, {len(texts)} texts, {len(tags)} tags, {len(word_tags)} word_tags")
        
        # Get initial counts for validation
        try:
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM words")
            initial_word_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM texts")
            initial_text_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM tags")
            initial_tag_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM word_tags")
            initial_word_tag_count = cursor.fetchone()[0]
            conn.close()
        except Exception as e:
            logging.warning(f"Error getting initial counts: {e}, assuming empty database")
            initial_word_count = initial_text_count = initial_tag_count = initial_word_tag_count = 0
        
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        failures = 0  # words/texts that errored on write; a nonzero count must not
                      # be reported as a clean sync — this is exactly what hid the
                      # missing-cloud_id bug (every word failed yet sync "succeeded").

        try:
            # Verify tables exist (they should, but check to be safe)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('words', 'texts', 'tags', 'word_tags')")
            existing_tables = {row[0] for row in cursor.fetchall()}
            required_tables = {'words', 'texts', 'tags', 'word_tags'}
            missing_tables = required_tables - existing_tables
            if missing_tables:
                raise Exception(f"Required tables are missing: {missing_tables}. Please initialize the database first.")
            
            tags_inserted = 0
            tags_skipped = 0
            words_inserted = 0
            words_updated = 0
            texts_inserted = 0
            texts_updated = 0
            word_tags_inserted = 0
            word_tags_skipped = 0
            
            # Pull tags first
            tag_id_mapping = {}  # Map cloud tag_id to local tag_id
            logging.info(f"Pulling {len(tags)} tags...")
            for tag in tags:
                cloud_tag_id = tag.get('tag_id')
                tag_name = tag.get('tag_name')
                if tag_name:
                    try:
                        # Check if tag exists locally
                        cursor.execute("SELECT tag_id FROM tags WHERE tag_name = ?", (tag_name,))
                        existing = cursor.fetchone()
                        if existing:
                            local_tag_id = existing[0]
                            tags_skipped += 1
                            logging.debug(f"Tag '{tag_name}' already exists locally with ID {local_tag_id}")
                        else:
                            # Insert new tag
                            cursor.execute("INSERT INTO tags (tag_id, tag_name) VALUES (?, ?)", (cloud_tag_id, tag_name))
                            local_tag_id = cloud_tag_id
                            tags_inserted += 1
                            logging.debug(f"Inserted tag '{tag_name}' with ID {cloud_tag_id}")
                        tag_id_mapping[cloud_tag_id] = local_tag_id
                    except sqlite3.IntegrityError as e:
                        logging.warning(f"Failed to pull tag {tag_name} (ID {cloud_tag_id}): Integrity error - {e}")
                        tags_skipped += 1
                    except Exception as e:
                        logging.error(f"Failed to pull tag {tag_name} (ID {cloud_tag_id}): {e}")
                        tags_skipped += 1
            
            logging.info(f"Tags: {tags_inserted} inserted, {tags_skipped} skipped")
            
            # Pull words
            logging.info(f"Pulling {len(words)} words...")
            for word in words:
                try:
                    cloud_id = word.get('ID') or word.get('id')
                    # Check if word exists (by cloud_id or content) for statistics
                    language1 = word.get('Language1')
                    word1 = word.get('Word1')
                    language2 = word.get('Language2')
                    word2 = word.get('Word2')
                    
                    exists = False
                    if cloud_id:
                        cursor.execute("SELECT ID FROM words WHERE cloud_id = ?", (cloud_id,))
                        exists = cursor.fetchone() is not None
                    if not exists and all([language1, word1, language2, word2]):
                        cursor.execute("""
                            SELECT ID FROM words 
                            WHERE Language1 = ? AND Word1 = ? AND Language2 = ? AND Word2 = ?
                        """, (language1, word1, language2, word2))
                        exists = cursor.fetchone() is not None
                    
                    if exists:
                        words_updated += 1
                    else:
                        words_inserted += 1
                    self._sync_word_to_local(cursor, word)
                except Exception as e:
                    failures += 1
                    logging.error(f"Failed to pull word ID {word.get('ID') or word.get('id')}: {e}")

            logging.info(f"Words: {words_inserted} inserted, {words_updated} updated")
            
            # Pull texts
            logging.info(f"Pulling {len(texts)} texts...")
            for text in texts:
                try:
                    text_id = text.get('ID') or text.get('id')
                    # Check if text exists
                    cursor.execute("SELECT ID FROM texts WHERE ID = ?", (text_id,))
                    exists = cursor.fetchone()
                    if exists:
                        texts_updated += 1
                    else:
                        texts_inserted += 1
                    self._sync_text_to_local(cursor, text)
                except Exception as e:
                    failures += 1
                    logging.error(f"Failed to pull text ID {text.get('ID') or text.get('id')}: {e}")

            logging.info(f"Texts: {texts_inserted} inserted, {texts_updated} updated")
            
            # Pull word_tags (update tag_ids using mapping)
            logging.info(f"Pulling {len(word_tags)} word_tag relationships...")
            for word_tag in word_tags:
                word_id = word_tag.get('word_id')
                cloud_tag_id = word_tag.get('tag_id')
                local_tag_id = tag_id_mapping.get(cloud_tag_id)
                
                if word_id and local_tag_id:
                    try:
                        cursor.execute("INSERT OR IGNORE INTO word_tags (word_id, tag_id) VALUES (?, ?)", 
                                      (word_id, local_tag_id))
                        if cursor.rowcount > 0:
                            word_tags_inserted += 1
                        else:
                            word_tags_skipped += 1
                    except Exception as e:
                        logging.warning(f"Failed to pull word_tag relationship (word {word_id}, tag {local_tag_id}): {e}")
                        word_tags_skipped += 1
                else:
                    logging.warning(f"Skipping word_tag: missing word_id ({word_id}) or tag_id ({local_tag_id})")
                    word_tags_skipped += 1
            
            logging.info(f"Word_tags: {word_tags_inserted} inserted, {word_tags_skipped} skipped")
            
            # Commit all changes
            conn.commit()
            logging.info("Transaction committed successfully")
            
            # Validate the sync by checking final counts
            cursor.execute("SELECT COUNT(*) FROM words")
            final_word_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM texts")
            final_text_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM tags")
            final_tag_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM word_tags")
            final_word_tag_count = cursor.fetchone()[0]
            
            word_diff = final_word_count - initial_word_count
            text_diff = final_text_count - initial_text_count
            tag_diff = final_tag_count - initial_tag_count
            word_tag_diff = final_word_tag_count - initial_word_tag_count
            
            logging.info(f"Sync validation - Words: {initial_word_count} -> {final_word_count} (diff: {word_diff}, expected: {words_inserted})")
            logging.info(f"Sync validation - Texts: {initial_text_count} -> {final_text_count} (diff: {text_diff}, expected: {texts_inserted})")
            logging.info(f"Sync validation - Tags: {initial_tag_count} -> {final_tag_count} (diff: {tag_diff}, expected: {tags_inserted})")
            logging.info(f"Sync validation - Word_tags: {initial_word_tag_count} -> {final_word_tag_count} (diff: {word_tag_diff}, expected: {word_tags_inserted})")
            
            # Warn if counts don't match expectations (allowing for updates)
            if word_diff < words_inserted:
                logging.warning(f"Word count increase ({word_diff}) is less than expected inserts ({words_inserted}) - some words may have failed to sync")
            if text_diff < texts_inserted:
                logging.warning(f"Text count increase ({text_diff}) is less than expected inserts ({texts_inserted}) - some texts may have failed to sync")
            
            logging.info("Finished pulling cloud data to local")
            
        except Exception as e:
            logging.error(f"Error pulling cloud data: {e}", exc_info=True)
            conn.rollback()
            logging.error("Transaction rolled back due to error")
            raise
        finally:
            conn.close()

        # Per-record failures above were only logged. If any occurred, the pull did
        # NOT fully succeed — raise so the caller leaves first_sync_completed/last_sync
        # unstamped and the UI reports an error instead of "Sync completed" over a
        # silent data loss (how the missing cloud_id column went unnoticed).
        if failures:
            raise SyncError(
                f"{failures} record(s) could not be saved to the local database "
                f"during the cloud pull — see the log for the failing rows.")

    def _merge_sync(self, local_words, local_texts, local_tags, local_word_tags,
                   cloud_words, cloud_texts, cloud_tags, cloud_word_tags):
        """Merge data from both sides, handling conflicts.
        
        Uses content-based matching for words to handle cases where IDs differ
        between local and cloud.
        """
        logging.info("Merging data from both sides...")
        
        # Create ID sets for comparison (for texts and backward compatibility)
        local_text_ids = {t.get('ID') for t in local_texts}
        cloud_text_ids = {t.get('ID') or t.get('id') for t in cloud_texts}
        
        # Create content-based index for words: (language1, word1, language2, word2) -> word
        def get_content_key(word):
            """Get content key for a word."""
            lang1 = word.get('Language1') or word.get('language1')
            w1 = word.get('Word1') or word.get('word1')
            lang2 = word.get('Language2') or word.get('language2')
            w2 = word.get('Word2') or word.get('word2')
            if all([lang1, w1, lang2, w2]):
                return (lang1, w1, lang2, w2)
            return None
        
        local_word_by_content = {}
        local_word_by_id = {}
        for word in local_words:
            word_id = word.get('ID')
            if word_id:
                local_word_by_id[word_id] = word
            content_key = get_content_key(word)
            if content_key:
                local_word_by_content[content_key] = word
        
        cloud_word_by_content = {}
        cloud_word_by_id = {}
        for word in cloud_words:
            word_id = word.get('ID') or word.get('id')
            if word_id:
                cloud_word_by_id[word_id] = word
            content_key = get_content_key(word)
            if content_key:
                cloud_word_by_content[content_key] = word
        
        # Words: merge by content (and ID for backward compatibility), last-write-wins
        word_updates = []
        word_inserts = []
        processed_local_words = set()
        
        # Process cloud words
        for word in cloud_words:
            word_id = word.get('ID') or word.get('id')
            content_key = get_content_key(word)
            local_word = None
            
            # Try to match by content first (primary method)
            if content_key and content_key in local_word_by_content:
                local_word = local_word_by_content[content_key]
                processed_local_words.add(local_word.get('ID'))
            # Fallback to ID-based matching (for backward compatibility)
            elif word_id and word_id in local_word_by_id:
                local_word = local_word_by_id[word_id]
                processed_local_words.add(word_id)
            
            if local_word:
                # Both have it - compare timestamps
                cloud_ts = word.get('edited_at') or word.get('created_at')
                local_ts = local_word.get('edited_at') or local_word.get('created_at')
                if self._compare_timestamps(cloud_ts, local_ts) >= 0:
                    word_updates.append(word)
            else:
                # Only in cloud - insert
                word_inserts.append(word)
        
        # Process local words not matched in cloud
        for word in local_words:
            word_id = word.get('ID')
            if word_id not in processed_local_words:
                # Check if it exists in cloud by content
                content_key = get_content_key(word)
                if content_key and content_key not in cloud_word_by_content:
                    # Not in cloud by content or ID - insert
                    word_inserts.append(word)
                elif content_key and content_key in cloud_word_by_content:
                    # Found by content but wasn't processed (shouldn't happen, but handle it)
                    cloud_word = cloud_word_by_content[content_key]
                    cloud_ts = cloud_word.get('edited_at') or cloud_word.get('created_at')
                    local_ts = word.get('edited_at') or word.get('created_at')
                    if self._compare_timestamps(cloud_ts, local_ts) < 0:
                        # Local is newer, but we already processed cloud version
                        # This is a conflict - cloud version wins (already added to updates)
                        pass
        
        # Same for texts
        text_updates = []
        text_inserts = []
        
        for text in cloud_texts:
            text_id = text.get('ID') or text.get('id')
            if text_id in local_text_ids:
                local_text = next((t for t in local_texts if t.get('ID') == text_id), None)
                if local_text:
                    cloud_ts = text.get('edited_at') or text.get('created_at')
                    local_ts = local_text.get('edited_at') or local_text.get('created_at')
                    if self._compare_timestamps(cloud_ts, local_ts) >= 0:
                        text_updates.append(text)
            else:
                text_inserts.append(text)
        
        for text in local_texts:
            text_id = text.get('ID')
            if text_id not in cloud_text_ids:
                text_inserts.append(text)
        
        # Apply changes to local
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            for word in word_updates + word_inserts:
                self._sync_word_to_local(cursor, word)
            
            for text in text_updates + text_inserts:
                self._sync_text_to_local(cursor, text)
            
            conn.commit()
        except Exception as e:
            logging.error(f"Error merging to local: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
        
        # Push local-only items to cloud (use content-based matching)
        for word in local_words:
            word_id = word.get('ID')
            content_key = get_content_key(word)
            
            # Check if word exists in cloud by content or ID
            exists_in_cloud = False
            if content_key and content_key in cloud_word_by_content:
                exists_in_cloud = True
            elif word_id and word_id in cloud_word_by_id:
                exists_in_cloud = True
            
            if not exists_in_cloud:
                try:
                    # Use upsert to insert (will check by content and insert if not found)
                    self.supabase.upsert_word(word)
                except Exception as e:
                    logging.warning(f"Failed to push word {word_id} to cloud: {e}")
        
        for text in local_texts:
            text_id = text.get('ID')
            if text_id not in cloud_text_ids:
                try:
                    self.supabase.insert_text(text, preserve_id=True)
                except Exception as e:
                    logging.warning(f"Failed to push text {text_id} to cloud: {e}")
        
        # Merge tags and word_tags (simplified - just sync both ways)
        self._merge_tags(local_tags, cloud_tags, local_word_tags, cloud_word_tags)
        
        logging.info("Finished merging data")
    
    def _merge_tags(self, local_tags, cloud_tags, local_word_tags, cloud_word_tags):
        """Merge tags and word_tags from both sides."""
        # This is simplified - in a full implementation, we'd handle tag name conflicts
        # For now, we'll sync tags by name and word_tags by (word_id, tag_id)
        logging.info("Merging tags and word_tags...")
        
        # Create tag name mappings
        local_tag_by_name = {t.get('tag_name'): t for t in local_tags}
        cloud_tag_by_name = {t.get('tag_name'): t for t in cloud_tags}
        
        # Sync tags to local
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        try:
            for tag in cloud_tags:
                tag_name = tag.get('tag_name')
                if tag_name:
                    cursor.execute("INSERT OR IGNORE INTO tags (tag_id, tag_name) VALUES (?, ?)",
                                  (tag.get('tag_id'), tag_name))
            
            # Sync word_tags to local
            for word_tag in cloud_word_tags:
                cursor.execute("INSERT OR IGNORE INTO word_tags (word_id, tag_id) VALUES (?, ?)",
                             (word_tag.get('word_id'), word_tag.get('tag_id')))
            
            conn.commit()
        except Exception as e:
            logging.error(f"Error merging tags: {e}")
            conn.rollback()
        finally:
            conn.close()
        
        # Push local tags to cloud
        for tag in local_tags:
            tag_name = tag.get('tag_name')
            if tag_name and tag_name not in cloud_tag_by_name:
                try:
                    self.supabase.insert_tag(tag_name, tag_id=tag.get('tag_id'))
                except Exception as e:
                    logging.warning(f"Failed to push tag {tag_name}: {e}")
        
        # Push local word_tags to cloud
        local_wt_set = {(wt.get('word_id'), wt.get('tag_id')) for wt in local_word_tags}
        cloud_wt_set = {(wt.get('word_id'), wt.get('tag_id')) for wt in cloud_word_tags}
        
        for word_tag in local_word_tags:
            key = (word_tag.get('word_id'), word_tag.get('tag_id'))
            if key not in cloud_wt_set:
                try:
                    self.supabase.add_tag_to_word(word_tag.get('word_id'), word_tag.get('tag_id'))
                except Exception as e:
                    logging.warning(f"Failed to push word_tag: {e}")
    
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
            ts1_clean = ts1.replace('Z', '+00:00') if 'Z' in ts1 else ts1
            ts2_clean = ts2.replace('Z', '+00:00') if 'Z' in ts2 else ts2
            dt1 = datetime.fromisoformat(ts1_clean)
            dt2 = datetime.fromisoformat(ts2_clean)
            
            # Normalize both to naive UTC for comparison
            # If timezone-aware, convert to UTC and remove timezone
            # If naive, assume it's UTC
            if dt1.tzinfo is not None:
                dt1 = dt1.astimezone(timezone.utc).replace(tzinfo=None)
            if dt2.tzinfo is not None:
                dt2 = dt2.astimezone(timezone.utc).replace(tzinfo=None)
            
            if dt1 < dt2:
                return -1
            elif dt1 > dt2:
                return 1
            return 0
        except (ValueError, AttributeError):
            try:
                # Try SQLite format
                dt1 = datetime.strptime(ts1, '%Y-%m-%d %H:%M:%S')
                dt2 = datetime.strptime(ts2, '%Y-%m-%d %H:%M:%S')
                if dt1 < dt2:
                    return -1
                elif dt1 > dt2:
                    return 1
                return 0
            except (ValueError, AttributeError):
                # Fallback to string comparison
                if ts1 < ts2:
                    return -1
                elif ts1 > ts2:
                    return 1
                return 0
    
    def _apply_cloud_to_local_with_conflict_check(self, changes: dict, pending_deletions: List[Dict[str, Any]], resolved_conflicts: set):
        """Apply cloud changes to local, skipping records that were deleted locally or resolved conflicts."""
        words_to_apply = changes.get('words', [])
        texts_to_apply = changes.get('texts', [])
        
        logging.info(f"Applying cloud changes to local: {len(words_to_apply)} words, {len(texts_to_apply)} texts")
        
        deletion_keys = {(d['table_name'], d['record_id']) for d in pending_deletions}
        # Combine deletion keys with resolved conflicts
        skip_keys = deletion_keys | resolved_conflicts
        
        logging.debug(f"Skipping {len(skip_keys)} records due to local deletions or resolved conflicts")
        
        # Get initial counts
        try:
            conn = sqlite3.connect(self.local_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM words")
            initial_word_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM texts")
            initial_text_count = cursor.fetchone()[0]
            conn.close()
        except Exception as e:
            logging.warning(f"Error getting initial counts: {e}")
            initial_word_count = initial_text_count = 0
        
        conn = sqlite3.connect(self.local_db)
        cursor = conn.cursor()
        
        words_applied = 0
        words_skipped = 0
        words_inserted = 0
        words_updated = 0
        texts_applied = 0
        texts_skipped = 0
        texts_inserted = 0
        texts_updated = 0
        
        try:
            # Apply words changes (skip if locally deleted or conflict resolved)
            for word in words_to_apply:
                word_id = word.get('ID') or word.get('id')
                key = ('words', word_id)
                
                # Skip if this record was deleted locally or conflict resolved (local wins)
                if key in skip_keys:
                    words_skipped += 1
                    logging.debug(f"Skipping cloud word {word_id} - locally deleted or conflict resolved")
                    continue
                
                # Check if word exists (by cloud_id or content) to track insert vs update
                language1 = word.get('Language1')
                word1 = word.get('Word1')
                language2 = word.get('Language2')
                word2 = word.get('Word2')
                
                exists = False
                if word_id:
                    cursor.execute("SELECT ID FROM words WHERE cloud_id = ?", (word_id,))
                    exists = cursor.fetchone() is not None
                if not exists and all([language1, word1, language2, word2]):
                    cursor.execute("""
                        SELECT ID FROM words 
                        WHERE Language1 = ? AND Word1 = ? AND Language2 = ? AND Word2 = ?
                    """, (language1, word1, language2, word2))
                    exists = cursor.fetchone() is not None
                
                if exists:
                    words_updated += 1
                else:
                    words_inserted += 1
                
                self._sync_word_to_local(cursor, word)
                words_applied += 1
            
            # Apply texts changes (skip if locally deleted or conflict resolved)
            for text in texts_to_apply:
                text_id = text.get('ID') or text.get('id')
                key = ('texts', text_id)
                
                # Skip if this record was deleted locally or conflict resolved (local wins)
                if key in skip_keys:
                    texts_skipped += 1
                    logging.debug(f"Skipping cloud text {text_id} - locally deleted or conflict resolved")
                    continue
                
                # Check if text exists to track insert vs update
                cursor.execute("SELECT ID FROM texts WHERE ID = ?", (text_id,))
                exists = cursor.fetchone()
                if exists:
                    texts_updated += 1
                else:
                    texts_inserted += 1
                
                self._sync_text_to_local(cursor, text)
                texts_applied += 1
            
            conn.commit()
            
            # Get final counts
            cursor.execute("SELECT COUNT(*) FROM words")
            final_word_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM texts")
            final_text_count = cursor.fetchone()[0]
            
            word_diff = final_word_count - initial_word_count
            text_diff = final_text_count - initial_text_count
            
            logging.info(f"Applied cloud changes: Words - {words_applied} applied ({words_inserted} inserted, {words_updated} updated), {words_skipped} skipped")
            logging.info(f"Applied cloud changes: Texts - {texts_applied} applied ({texts_inserted} inserted, {texts_updated} updated), {texts_skipped} skipped")
            logging.info(f"Count changes: Words {initial_word_count} -> {final_word_count} (diff: {word_diff}), Texts {initial_text_count} -> {final_text_count} (diff: {text_diff})")
            
        except Exception as e:
            logging.error(f"Error applying cloud changes to local: {e}", exc_info=True)
            conn.rollback()
            raise
        finally:
            conn.close()

