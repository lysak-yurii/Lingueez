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

import os
from supabase import create_client, Client
from typing import Optional, List, Dict, Any
import logging
from dotenv import load_dotenv
import time
import threading

load_dotenv()


# Central hosted Lingueez project. Shipped in source on purpose: the anon key is a
# PUBLIC client key and Row-Level Security isolates each signed-in user's rows, so
# it is safe to distribute. Advanced users can override both via .env / env vars
# (SUPABASE_URL / SUPABASE_KEY) to point at their own project.
DEFAULT_SUPABASE_URL = "https://dtyrmkynrideeknsdlrn.supabase.co"
DEFAULT_SUPABASE_KEY ="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0eXJta3lucmlkZWVrbnNkbHJuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODE4Njc1MTQsImV4cCI6MjA5NzQ0MzUxNH0.dds5SyMBN9u-0TUumB2nSCx68FJfpm3n63fLq1n9o10"


def _client_options():
    """Desktop-tuned client options. PKCE flow is required for the Google OAuth
    loopback (``exchange_code_for_session``); session persistence is handled by
    AuthManager via the OS keychain, not by gotrue's own storage."""
    try:
        from supabase import ClientOptions
    except ImportError:  # older layout
        from supabase.lib.client_options import ClientOptions
    return ClientOptions(
        flow_type="pkce",
        auto_refresh_token=True,
        persist_session=False,
    )


class SupabaseClient:
    """Wrapper for Supabase client with methods for dictionary operations.

    Holds an optional bearer token (the signed-in user's access token). Every
    rebuild of the underlying client re-applies it, so Row-Level Security keeps
    resolving ``auth.uid()`` to the logged-in user across credential changes.
    """

    def __init__(self):
        self.url = os.getenv('SUPABASE_URL') or DEFAULT_SUPABASE_URL
        self.key = os.getenv('SUPABASE_KEY') or DEFAULT_SUPABASE_KEY
        self.client: Optional[Client] = None
        self._auth_token: Optional[str] = None
        self._connection_cache = {'result': None, 'timestamp': 0, 'lock': threading.Lock()}
        self._cache_duration = 2  # Cache successful connections for 2 seconds only

        if self.url and self.key:
            try:
                self.client = create_client(self.url, self.key, options=_client_options())
                self._apply_token()
                logging.info("Supabase client initialized successfully")
            except Exception as e:
                logging.error(f"Failed to initialize Supabase client: {e}")
                self.client = None
        else:
            logging.warning("Supabase credentials not found in environment variables")

    def reconfigure(self):
        """Rebuild the client from the current environment (e.g. after the
        Supabase credentials change in Settings) and clear the connection cache,
        so an existing instance adopts new creds without an app restart."""
        self.url = os.getenv('SUPABASE_URL') or DEFAULT_SUPABASE_URL
        self.key = os.getenv('SUPABASE_KEY') or DEFAULT_SUPABASE_KEY
        self.client = None
        if self.url and self.key:
            try:
                self.client = create_client(self.url, self.key, options=_client_options())
                self._apply_token()
                logging.info("Supabase client reconfigured")
            except Exception as e:
                logging.error(f"Failed to reconfigure Supabase client: {e}")
                self.client = None
        with self._connection_cache['lock']:
            self._connection_cache['result'] = None
            self._connection_cache['timestamp'] = 0

    def set_auth_token(self, token: Optional[str]):
        """Attach (or clear) the signed-in user's access token. Passing None
        reverts PostgREST to the anonymous key. Stored so it survives a later
        reconfigure()."""
        self._auth_token = token
        self._apply_token()

    def _apply_token(self):
        """Point PostgREST at the current token (user JWT, or anon key when
        logged out) so RLS-scoped requests carry the right identity."""
        if self.client is None:
            return
        try:
            self.client.postgrest.auth(self._auth_token or self.key)
        except Exception as e:
            logging.warning(f"Could not apply auth token to PostgREST: {e}")

    def is_connected(self) -> bool:
        """Check if Supabase client is connected and can reach the server.
        
        This method actually tests network connectivity by making a lightweight
        API call, not just checking if the client object exists.
        """
        # First check if client exists
        if self.client is None:
            return False
        
        # Check cache first to avoid excessive network calls
        # But use shorter cache duration for more responsive status updates
        with self._connection_cache['lock']:
            current_time = time.time()
            cache_age = current_time - self._connection_cache['timestamp']
            
            if (self._connection_cache['result'] is not None and 
                cache_age < self._cache_duration):
                return self._connection_cache['result']
        
        # Perform actual connectivity test
        # CRITICAL: Default to False - only return True if we successfully execute a query
        # ANY exception means we cannot reach Supabase, so we are NOT connected
        result = False
        try:
            # Make a lightweight query to test connectivity
            # Use a simple select with limit 1 to minimize data transfer
            # This will raise an exception if there's no internet connection
            test_response = self.client.table('words').select('id').limit(1).execute()
            
            # If we get here without exception, we have connectivity
            # (even if result is empty, the network call succeeded)
            result = True
            logging.debug("Connectivity test successful - Supabase is reachable")
            
        except Exception as e:
            # ANY exception during the query means we cannot reach Supabase
            # This includes network errors, timeouts, DNS failures, connection refused, etc.
            # We treat ALL exceptions as "not connected" because if we can't query,
            # we can't sync, regardless of the specific error type
            result = False
            error_type = type(e).__name__
            error_str = str(e)
            logging.info(f"Connectivity test failed - cannot reach Supabase ({error_type}): {error_str[:100]}")
        
        # Update cache - clear cache if we got False to force fresh check next time
        with self._connection_cache['lock']:
            if result:
                # Cache successful connections
                self._connection_cache['result'] = result
                self._connection_cache['timestamp'] = time.time()
            else:
                # Don't cache failures - always check fresh when offline
                # This ensures we detect when connection is restored quickly
                self._connection_cache['result'] = None
                self._connection_cache['timestamp'] = 0
        
        return result
    
    def test_connection_with_error_info(self):
        """Test connection and return (is_connected, error_message).
        
        Returns:
            tuple: (is_connected: bool, error_message: Optional[str])
            error_message is None if connected, or a descriptive message if not
        """
        if self.client is None:
            return False, "Please check your internet connection or credentials."
        
        try:
            test_response = self.client.table('words').select('id').limit(1).execute()
            return True, None
        except Exception as e:
            # For any connection failure, suggest checking both internet and credentials
            # since we can't reliably distinguish between network errors and credential errors
            return False, "Please check your internet connection or credentials."
    
    def _map_to_sqlite_format(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Map Supabase column names (lowercase/snake_case) to SQLite format (PascalCase)."""
        mapping = {
            'id': 'ID',
            'row_number': 'RowNumber',
            'source': 'Source',
            'definition': 'Definition',
            'definition2': 'Definition2',
            'status': 'Status',
            'language1': 'Language1',
            'word1': 'Word1',
            'language2': 'Language2',
            'word2': 'Word2',
            'created_at': 'created_at',
            'edited_at': 'edited_at',
            'deleted_at': 'deleted_at',
            'favorite': 'favorite',
            'title': 'Title',
            'words': 'Words',
            'text': 'Text',
            'language': 'Language',
            'category': 'Category',
            'level': 'Level',
            'tag_id': 'tag_id',
            'tag_name': 'tag_name',
            'word_id': 'word_id',
        }
        result = {}
        for key, value in data.items():
            mapped_key = mapping.get(key, key)
            result[mapped_key] = value
        return result
    
    def _map_to_supabase_format(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Map SQLite column names (PascalCase) to Supabase format (lowercase/snake_case)."""
        reverse_mapping = {
            'ID': 'id',
            'RowNumber': 'row_number',
            'Source': 'source',
            'Definition': 'definition',
            'Definition2': 'definition2',
            'Status': 'status',
            'Language1': 'language1',
            'Word1': 'word1',
            'Language2': 'language2',
            'Word2': 'word2',
            'created_at': 'created_at',
            'edited_at': 'edited_at',
            'deleted_at': 'deleted_at',
            'favorite': 'favorite',
            'Title': 'title',
            'Words': 'words',
            'Text': 'text',
            'Language': 'language',
            'Category': 'category',
            'Level': 'level',
            'tag_id': 'tag_id',
            'tag_name': 'tag_name',
            'word_id': 'word_id',
        }
        # Local-only / server-owned fields that should never be sent to Supabase.
        # user_id is stamped server-side by the `default auth.uid()` column (and
        # the RLS `with check` policy rejects a mismatched value), so we never
        # send it from the client even if a cloud read round-trips it back.
        local_only_fields = {'cloud_id', 'Cloud_id', 'CLOUD_ID', 'user_id'}
        
        result = {}
        for key, value in data.items():
            # Skip local-only fields that don't exist in Supabase
            if key in local_only_fields:
                continue
            mapped_key = reverse_mapping.get(key, key.lower())
            result[mapped_key] = value
        return result
    
    # Words operations
    def get_words(self) -> List[Dict[str, Any]]:
        """Get all words from Supabase with pagination support (excluding soft-deleted records)."""
        if not self.client:
            return []
        try:
            all_words = []
            page_size = 1000
            offset = 0
            
            while True:
                # Order by created_at descending for consistent ordering
                # Exclude soft-deleted records (deleted_at IS NULL)
                # Secondary sort by ID will be handled in DataFrame sorting
                response = self.client.table('words').select('*').is_('deleted_at', 'null').order('created_at', desc=True).range(offset, offset + page_size - 1).execute()
                if not response.data:
                    break
                all_words.extend([self._map_to_sqlite_format(word) for word in response.data])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_words
        except Exception as e:
            logging.error(f"Error fetching words from Supabase: {e}")
            return []
    
    def get_word(self, word_id: int) -> Optional[Dict[str, Any]]:
        """Get a single word by ID."""
        if not self.client:
            return None
        try:
            response = self.client.table('words').select('*').eq('id', word_id).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error fetching word {word_id} from Supabase: {e}")
            return None
    
    def find_word_by_content(self, language1: str, word1: str, language2: str, word2: str) -> Optional[Dict[str, Any]]:
        """Find a word in Supabase by content (Language1, Word1, Language2, Word2).
        
        Args:
            language1: First language
            word1: First word
            language2: Second language
            word2: Second word
            
        Returns:
            Word dict if found, None otherwise. Excludes soft-deleted words.
        """
        if not self.client:
            return None
        try:
            # Query by content, excluding soft-deleted words
            response = self.client.table('words').select('*').eq('language1', language1).eq('word1', word1).eq('language2', language2).eq('word2', word2).is_('deleted_at', 'null').execute()
            if response.data and len(response.data) > 0:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error finding word by content in Supabase: {e}")
            return None

    def find_soft_deleted_word(self, word1: str, word2: str) -> Optional[Dict[str, Any]]:
        """Find a soft-deleted word matching the (word1, word2) pair.

        Matches on word1+word2 only because that is what the
        words_word1_word2_key unique constraint covers — any row with this
        pair, deleted or not, blocks an insert.

        Returns:
            Word dict if found, None otherwise.
        """
        if not self.client:
            return None
        try:
            response = (self.client.table('words').select('*')
                        .eq('word1', word1).eq('word2', word2)
                        .not_.is_('deleted_at', 'null').execute())
            if response.data and len(response.data) > 0:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error finding soft-deleted word in Supabase: {e}")
            return None

    def restore_word_with_data(self, word_id: int, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Restore a soft-deleted word (deleted_at = NULL) and overwrite it with new data."""
        if not self.client:
            return None
        try:
            mapped_data = self._map_to_supabase_format(word_data)
            mapped_data.pop('id', None)
            mapped_data['deleted_at'] = None
            from datetime import datetime, timezone
            mapped_data['edited_at'] = datetime.now(timezone.utc).isoformat()
            response = self.client.table('words').update(mapped_data).eq('id', word_id).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error restoring soft-deleted word {word_id} in Supabase: {e}")
            return None

    def upsert_word(self, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert or update a word in Supabase based on content matching.
        
        First checks if a word with the same content (Language1, Word1, Language2, Word2) exists.
        If found, updates it. If not found, inserts it (with auto-generated ID).
        
        Args:
            word_data: Word data to insert or update
            
        Returns:
            Word dict if successful, None otherwise
        """
        if not self.client:
            return None
        
        # Extract content fields for matching
        language1 = word_data.get('Language1') or word_data.get('language1')
        word1 = word_data.get('Word1') or word_data.get('word1')
        language2 = word_data.get('Language2') or word_data.get('language2')
        word2 = word_data.get('Word2') or word_data.get('word2')
        
        if not all([language1, word1, language2, word2]):
            logging.error("Cannot upsert word: missing required content fields (Language1, Word1, Language2, Word2)")
            return None
        
        # Try to find existing word by content
        existing_word = self.find_word_by_content(language1, word1, language2, word2)
        
        if existing_word:
            # Word exists, update it
            cloud_id = existing_word.get('ID') or existing_word.get('id')
            if cloud_id:
                logging.debug(f"Found existing word with content ({language1}, {word1}, {language2}, {word2}), updating ID {cloud_id}")
                return self.update_word(cloud_id, word_data)
            else:
                logging.warning(f"Found existing word but couldn't get ID, falling back to insert")

        # No live match — but a soft-deleted row with the same word pair still
        # blocks an insert (the unique constraint covers binned rows too).
        # Restore that row with the new data instead of failing with a 409.
        deleted_word = self.find_soft_deleted_word(word1, word2)
        if deleted_word:
            cloud_id = deleted_word.get('ID') or deleted_word.get('id')
            if cloud_id:
                logging.info(f"Word ({word1}, {word2}) exists soft-deleted in cloud "
                             f"(ID {cloud_id}) — restoring it with the new data")
                return self.restore_word_with_data(cloud_id, word_data)

        # Word doesn't exist, insert it (auto-generate ID)
        logging.debug(f"No existing word found with content ({language1}, {word1}, {language2}, {word2}), inserting new")
        return self.insert_word(word_data, preserve_id=False)
    
    def insert_word(self, word_data: Dict[str, Any], preserve_id: bool = False) -> Optional[Dict[str, Any]]:
        """Insert a new word into Supabase.
        
        Args:
            word_data: Word data to insert
            preserve_id: If True, preserve the ID from word_data (for migration). 
                        If False, let Supabase auto-generate ID (default).
        """
        if not self.client:
            return None
        try:
            mapped_data = self._map_to_supabase_format(word_data)
            # Remove ID if it's None or 0, unless preserve_id is True
            if not preserve_id:
                if 'id' in mapped_data and (mapped_data['id'] is None or mapped_data['id'] == 0):
                    mapped_data.pop('id', None)
            # If preserve_id is True, keep the ID (for migration)
            response = self.client.table('words').insert(mapped_data).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error inserting word into Supabase: {e}")
            return None
    
    def update_word(self, word_id: int, word_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a word in Supabase."""
        if not self.client:
            return None
        try:
            mapped_data = self._map_to_supabase_format(word_data)
            # Remove ID from update data
            mapped_data.pop('id', None)
            mapped_data.pop('ID', None)
            # Set edited_at to current timestamp
            from datetime import datetime, timezone
            mapped_data['edited_at'] = datetime.now(timezone.utc).isoformat()
            response = self.client.table('words').update(mapped_data).eq('id', word_id).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error updating word {word_id} in Supabase: {e}")
            return None
    
    def delete_word(self, word_id: int) -> bool:
        """Soft delete a word from Supabase (sets deleted_at timestamp)."""
        if not self.client:
            return False
        try:
            from datetime import datetime, timezone
            # Use soft delete: set deleted_at timestamp instead of hard delete
            self.client.table('words').update({'deleted_at': datetime.now(timezone.utc).isoformat()}).eq('id', word_id).execute()
            return True
        except Exception as e:
            logging.error(f"Error deleting word {word_id} from Supabase: {e}")
            return False
    
    # Texts operations
    def get_texts(self) -> List[Dict[str, Any]]:
        """Get all texts from Supabase with pagination support (excluding soft-deleted records)."""
        if not self.client:
            return []
        try:
            all_texts = []
            page_size = 1000
            offset = 0
            
            while True:
                # Exclude soft-deleted records (deleted_at IS NULL)
                response = self.client.table('texts').select('*').is_('deleted_at', 'null').range(offset, offset + page_size - 1).execute()
                if not response.data:
                    break
                all_texts.extend([self._map_to_sqlite_format(text) for text in response.data])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_texts
        except Exception as e:
            logging.error(f"Error fetching texts from Supabase: {e}")
            return []
    
    def get_text(self, text_id: int) -> Optional[Dict[str, Any]]:
        """Get a single text by ID."""
        if not self.client:
            return None
        try:
            response = self.client.table('texts').select('*').eq('id', text_id).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error fetching text {text_id} from Supabase: {e}")
            return None
    
    def insert_text(self, text_data: Dict[str, Any], preserve_id: bool = False) -> Optional[Dict[str, Any]]:
        """Insert a new text into Supabase.
        
        Args:
            text_data: Text data to insert
            preserve_id: If True, preserve the ID from text_data (for migration). 
                        If False, let Supabase auto-generate ID (default).
        """
        if not self.client:
            return None
        try:
            mapped_data = self._map_to_supabase_format(text_data)
            if not preserve_id:
                if 'id' in mapped_data and (mapped_data['id'] is None or mapped_data['id'] == 0):
                    mapped_data.pop('id', None)
            response = self.client.table('texts').insert(mapped_data).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error inserting text into Supabase: {e}")
            return None
    
    def update_text(self, text_id: int, text_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a text in Supabase."""
        if not self.client:
            return None
        try:
            mapped_data = self._map_to_supabase_format(text_data)
            mapped_data.pop('id', None)
            mapped_data.pop('ID', None)
            from datetime import datetime, timezone
            mapped_data['edited_at'] = datetime.now(timezone.utc).isoformat()
            response = self.client.table('texts').update(mapped_data).eq('id', text_id).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error updating text {text_id} in Supabase: {e}")
            return None
    
    def delete_text(self, text_id: int) -> bool:
        """Soft delete a text from Supabase (sets deleted_at timestamp)."""
        if not self.client:
            return False
        try:
            from datetime import datetime, timezone
            # Use soft delete: set deleted_at timestamp instead of hard delete
            self.client.table('texts').update({'deleted_at': datetime.now(timezone.utc).isoformat()}).eq('id', text_id).execute()
            return True
        except Exception as e:
            logging.error(f"Error deleting text {text_id} from Supabase: {e}")
            return False
    
    # Tags operations
    def get_tags(self) -> List[Dict[str, Any]]:
        """Get all tags from Supabase with pagination support."""
        if not self.client:
            return []
        try:
            all_tags = []
            page_size = 1000
            offset = 0
            
            while True:
                response = self.client.table('tags').select('*').range(offset, offset + page_size - 1).execute()
                if not response.data:
                    break
                all_tags.extend([self._map_to_sqlite_format(tag) for tag in response.data])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_tags
        except Exception as e:
            logging.error(f"Error fetching tags from Supabase: {e}")
            return []
    
    def get_tag(self, tag_id: int) -> Optional[Dict[str, Any]]:
        """Get a single tag by ID."""
        if not self.client:
            return None
        try:
            response = self.client.table('tags').select('*').eq('tag_id', tag_id).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error fetching tag {tag_id} from Supabase: {e}")
            return None
    
    def insert_tag(self, tag_name: str, tag_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Insert a new tag into Supabase.
        
        Args:
            tag_name: Name of the tag
            tag_id: Optional tag ID to preserve (for migration). If None, auto-generate.
        """
        if not self.client:
            return None
        try:
            tag_data = {'tag_name': tag_name}
            if tag_id is not None:
                tag_data['tag_id'] = tag_id
            response = self.client.table('tags').insert(tag_data).execute()
            if response.data:
                return self._map_to_sqlite_format(response.data[0])
            return None
        except Exception as e:
            logging.error(f"Error inserting tag into Supabase: {e}")
            return None
    
    def reset_sequence(self, table_name: str, sequence_name: str = None):
        """Reset PostgreSQL sequence after inserting with explicit IDs.
        
        This should be called after migration to ensure future auto-generated IDs
        don't conflict with manually inserted IDs.
        """
        if not self.client:
            return False
        try:
            if sequence_name is None:
                # Default sequence naming: tablename_id_seq
                sequence_name = f"{table_name}_id_seq"
            
            # Get max ID from table
            if table_name == 'words':
                max_id_result = self.client.table('words').select('id').order('id', desc=True).limit(1).execute()
            elif table_name == 'texts':
                max_id_result = self.client.table('texts').select('id').order('id', desc=True).limit(1).execute()
            elif table_name == 'tags':
                max_id_result = self.client.table('tags').select('tag_id').order('tag_id', desc=True).limit(1).execute()
            else:
                return False
            
            if max_id_result.data:
                max_id = max_id_result.data[0].get('id') or max_id_result.data[0].get('tag_id', 0)
                # Reset sequence to max_id + 1
                # Note: This requires using RPC or direct SQL, which Supabase client might not support directly
                # We'll log a warning and provide SQL to run manually
                logging.info(f"To reset sequence for {table_name}, run in Supabase SQL editor:")
                logging.info(f"SELECT setval('{sequence_name}', {max_id + 1}, false);")
                return True
            return False
        except Exception as e:
            logging.warning(f"Could not reset sequence for {table_name}: {e}")
            logging.info(f"After migration, manually reset sequence in Supabase SQL editor:")
            logging.info(f"SELECT setval('{sequence_name or table_name + '_id_seq'}', (SELECT MAX(id) FROM {table_name}) + 1, false);")
            return False
    
    # Word_tags operations
    def get_word_tags(self, word_id: int) -> List[Dict[str, Any]]:
        """Get all tags for a word."""
        if not self.client:
            return []
        try:
            response = self.client.table('word_tags').select('*').eq('word_id', word_id).execute()
            return [self._map_to_sqlite_format(wt) for wt in response.data]
        except Exception as e:
            logging.error(f"Error fetching word tags for word {word_id} from Supabase: {e}")
            return []
    
    def get_all_word_tags(self) -> List[Dict[str, Any]]:
        """Get all word-tag relationships from Supabase with pagination support."""
        if not self.client:
            return []
        try:
            all_word_tags = []
            page_size = 1000
            offset = 0
            
            while True:
                response = self.client.table('word_tags').select('*').range(offset, offset + page_size - 1).execute()
                if not response.data:
                    break
                all_word_tags.extend([self._map_to_sqlite_format(wt) for wt in response.data])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_word_tags
        except Exception as e:
            logging.error(f"Error fetching all word tags from Supabase: {e}")
            return []
    
    def add_tag_to_word(self, word_id: int, tag_id: int) -> bool:
        """Add a tag to a word."""
        if not self.client:
            return False
        try:
            self.client.table('word_tags').insert({'word_id': word_id, 'tag_id': tag_id}).execute()
            return True
        except Exception as e:
            logging.error(f"Error adding tag {tag_id} to word {word_id} in Supabase: {e}")
            return False
    
    def remove_tag_from_word(self, word_id: int, tag_id: int) -> bool:
        """Remove a tag from a word."""
        if not self.client:
            return False
        try:
            self.client.table('word_tags').delete().eq('word_id', word_id).eq('tag_id', tag_id).execute()
            return True
        except Exception as e:
            logging.error(f"Error removing tag {tag_id} from word {word_id} in Supabase: {e}")
            return False
    
    def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag from Supabase."""
        if not self.client:
            return False
        try:
            self.client.table('tags').delete().eq('tag_id', tag_id).execute()
            return True
        except Exception as e:
            logging.error(f"Error deleting tag {tag_id} from Supabase: {e}")
            return False
    
    def get_changes_since(self, table_name: str, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get changes from a table since a given timestamp (excluding soft-deleted records).
        
        Args:
            table_name: Name of the table ('words', 'texts', 'tags', or 'word_tags')
            timestamp: ISO format timestamp. If None, returns all records.
            
        Returns:
            List of records that have changed since the timestamp, mapped to SQLite format.
        """
        if not self.client:
            logging.warning(f"Cannot fetch changes from {table_name}: Supabase client not initialized")
            return []
        
        try:
            all_records = []
            page_size = 1000
            offset = 0
            
            # Tags and word_tags don't have deleted_at or edited_at, so handle differently
            if table_name in ['tags', 'word_tags']:
                # For tags and word_tags, get all records (no timestamp filtering)
                # The sync manager will handle deduplication
                logging.debug(f"Fetching all {table_name} records (no timestamp filtering for this table)")
                while True:
                    response = self.client.table(table_name).select('*').range(offset, offset + page_size - 1).execute()
                    if not response.data:
                        break
                    all_records.extend(response.data)
                    if len(response.data) < page_size:
                        break
                    offset += page_size
            else:
                # For words and texts: exclude soft-deleted records and filter by timestamp
                # Exclude soft-deleted records
                if timestamp is None:
                    logging.info(f"Fetching ALL {table_name} records (timestamp is None - no filtering)")
                else:
                    logging.debug(f"Fetching {table_name} records (excluding soft-deleted, timestamp={timestamp})")
                
                # Fetch all non-deleted records with pagination
                records_fetched = 0
                while True:
                    query = self.client.table(table_name).select('*').is_('deleted_at', 'null')
                    response = query.range(offset, offset + page_size - 1).execute()
                    
                    if not response.data:
                        break
                    
                    batch_size = len(response.data)
                    all_records.extend(response.data)
                    records_fetched += batch_size
                    
                    if len(response.data) < page_size:
                        break
                    offset += page_size
                
                logging.info(f"Fetched {records_fetched} non-deleted {table_name} records from cloud")
                logging.debug(f"[SYNC DEBUG] Fetched {len(all_records)} {table_name} records from Supabase")
                if len(all_records) > 0:
                    logging.debug(f"[SYNC DEBUG] First record: {all_records[0]}")
                
                # Filter by timestamp in Python (more reliable than complex PostgREST filters)
                if timestamp:
                    try:
                        from datetime import datetime
                        # Parse timestamp for comparison
                        try:
                            # Try ISO format first (handles both with and without microseconds)
                            if 'T' in timestamp:
                                # ISO format: try with timezone first, then without
                                try:
                                    ts_dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00') if 'Z' in timestamp else timestamp)
                                except ValueError:
                                    # Try without timezone
                                    ts_dt = datetime.fromisoformat(timestamp.split('+')[0].split('Z')[0])
                            else:
                                # SQLite format
                                ts_dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                        except (ValueError, AttributeError) as parse_error:
                            logging.error(f"Could not parse last_sync timestamp '{timestamp}': {parse_error}")
                            # If we can't parse the timestamp, return all records to be safe
                            logging.warning(f"Returning all {table_name} records due to timestamp parse error")
                        else:
                            # Ensure timestamp is in UTC for comparison
                            # If it has timezone, convert to UTC. If naive, assume it's UTC (Supabase stores in UTC).
                            from datetime import timezone
                            logging.debug(f"[SYNC DEBUG] Parsing last_sync timestamp: '{timestamp}', parsed as: {ts_dt}, tzinfo: {ts_dt.tzinfo}")
                            if ts_dt.tzinfo is not None:
                                ts_dt = ts_dt.astimezone(timezone.utc).replace(tzinfo=None)
                                logging.debug(f"[SYNC DEBUG] Converted timezone-aware to naive UTC: {ts_dt}")
                            else:
                                logging.debug(f"[SYNC DEBUG] Timestamp is naive, treating as UTC: {ts_dt}")
                            
                            logging.info(f"Filtering {table_name} records: looking for records modified/created after {ts_dt} (last_sync: {timestamp})")
                            logging.debug(f"[SYNC DEBUG] Will filter {len(all_records)} records, looking for timestamps >= {ts_dt}")
                            if len(all_records) > 0:
                                sample_record = all_records[0]
                                logging.debug(f"[SYNC DEBUG] Sample record before filtering: ID={sample_record.get('id')}, edited_at={sample_record.get('edited_at')}, created_at={sample_record.get('created_at')}")
                            
                            filtered_records = []
                            records_checked = 0
                            records_with_edited_at = 0
                            records_with_created_at_only = 0
                            
                            for record in all_records:
                                records_checked += 1
                                # Check if record was modified/created since timestamp
                                # Logic: edited_at >= timestamp OR (edited_at IS NULL AND created_at >= timestamp)
                                # Use >= to catch records modified/created at or after last sync
                                edited_at = record.get('edited_at')
                                created_at = record.get('created_at')
                                
                                # Try to parse edited_at first (takes precedence)
                                record_id = record.get('id') or record.get('ID')
                                record_ts = None
                                edited_at_parsed = False
                                if edited_at:
                                    try:
                                        # Handle ISO format with or without timezone
                                        edited_at_clean = edited_at.replace('Z', '+00:00') if 'Z' in str(edited_at) else str(edited_at)
                                        record_ts = datetime.fromisoformat(edited_at_clean)
                                        original_record_ts = record_ts
                                        # Convert to UTC first, then normalize to naive datetime for comparison
                                        if record_ts.tzinfo is not None:
                                            from datetime import timezone
                                            record_ts = record_ts.astimezone(timezone.utc).replace(tzinfo=None)
                                        logging.debug(f"[SYNC DEBUG] Record {record_id}: edited_at='{edited_at}' -> parsed as {original_record_ts} -> normalized to {record_ts}")
                                        edited_at_parsed = True
                                    except (ValueError, AttributeError):
                                        try:
                                            # Parse as naive datetime - assume it's UTC (Supabase stores in UTC)
                                            record_ts = datetime.strptime(str(edited_at), '%Y-%m-%d %H:%M:%S')
                                            # No conversion needed - naive datetime assumed to be UTC
                                            edited_at_parsed = True
                                        except (ValueError, AttributeError):
                                            # Can't parse edited_at, log warning but continue
                                            logging.debug(f"Could not parse edited_at '{edited_at}' for {table_name} record ID {record.get('id') or record.get('ID')}, using created_at instead")
                                
                                # If edited_at was successfully parsed, use it for comparison
                                if edited_at_parsed and record_ts:
                                    records_with_edited_at += 1
                                    # Use >= to get records modified at or after last sync
                                    # (>= is safer to ensure we don't miss records due to timing precision)
                                    try:
                                        comparison_result = record_ts >= ts_dt
                                        logging.debug(f"[SYNC DEBUG] Record {record_id}: Comparing edited_at {record_ts} >= {ts_dt} = {comparison_result}")
                                        if comparison_result:
                                            logging.debug(f"[SYNC DEBUG] Record {record_id}: INCLUDED (edited_at >= last_sync)")
                                            filtered_records.append(record)
                                        else:
                                            logging.debug(f"[SYNC DEBUG] Record {record_id}: EXCLUDED (edited_at < last_sync)")
                                    except (TypeError, ValueError) as compare_error:
                                        # Comparison failed - include record to be safe
                                        logging.warning(f"Timestamp comparison failed for {table_name} record ID {record_id}: {compare_error}, including record")
                                        filtered_records.append(record)
                                elif not edited_at and created_at:
                                    # edited_at is NULL, check created_at
                                    records_with_created_at_only += 1
                                    try:
                                        created_at_clean = created_at.replace('Z', '+00:00') if 'Z' in str(created_at) else str(created_at)
                                        created_ts = datetime.fromisoformat(created_at_clean)
                                        original_created_ts = created_ts
                                        # Convert to UTC first, then normalize to naive datetime for comparison
                                        if created_ts.tzinfo is not None:
                                            from datetime import timezone
                                            created_ts = created_ts.astimezone(timezone.utc).replace(tzinfo=None)
                                        logging.debug(f"[SYNC DEBUG] Record {record_id}: created_at='{created_at}' -> parsed as {original_created_ts} -> normalized to {created_ts}")
                                    except (ValueError, AttributeError):
                                        try:
                                            # Parse as naive datetime - assume it's UTC (Supabase stores in UTC)
                                            created_ts = datetime.strptime(str(created_at), '%Y-%m-%d %H:%M:%S')
                                            # No conversion needed - naive datetime assumed to be UTC
                                        except (ValueError, AttributeError):
                                            # Can't parse either timestamp, include record to be safe
                                            logging.warning(f"Could not parse timestamps for {table_name} record ID {record.get('id') or record.get('ID')}, including it to be safe")
                                            filtered_records.append(record)
                                            continue
                                    
                                    # Use >= to get records created at or after last sync
                                    if created_ts:
                                        try:
                                            comparison_result = created_ts >= ts_dt
                                            logging.debug(f"[SYNC DEBUG] Record {record_id}: Comparing created_at {created_ts} >= {ts_dt} = {comparison_result}")
                                            if comparison_result:
                                                logging.debug(f"[SYNC DEBUG] Record {record_id}: INCLUDED (created_at >= last_sync)")
                                                filtered_records.append(record)
                                            else:
                                                logging.debug(f"[SYNC DEBUG] Record {record_id}: EXCLUDED (created_at < last_sync)")
                                        except (TypeError, ValueError) as compare_error:
                                            # Comparison failed - include record to be safe
                                            logging.warning(f"Timestamp comparison failed for {table_name} record ID {record_id}: {compare_error}, including record")
                                            filtered_records.append(record)
                                elif edited_at and not edited_at_parsed and created_at:
                                    # edited_at exists but couldn't be parsed, fall back to created_at
                                    logging.debug(f"[SYNC DEBUG] Record {record_id}: edited_at could not be parsed, falling back to created_at")
                                    try:
                                        created_at_clean = created_at.replace('Z', '+00:00') if 'Z' in str(created_at) else str(created_at)
                                        created_ts = datetime.fromisoformat(created_at_clean)
                                        original_created_ts = created_ts
                                        # Convert to UTC first, then normalize to naive datetime for comparison
                                        if created_ts.tzinfo is not None:
                                            from datetime import timezone
                                            created_ts = created_ts.astimezone(timezone.utc).replace(tzinfo=None)
                                        logging.debug(f"[SYNC DEBUG] Record {record_id}: fallback created_at='{created_at}' -> parsed as {original_created_ts} -> normalized to {created_ts}")
                                    except (ValueError, AttributeError):
                                        try:
                                            # Parse as naive datetime - assume it's UTC (Supabase stores in UTC)
                                            created_ts = datetime.strptime(str(created_at), '%Y-%m-%d %H:%M:%S')
                                            # No conversion needed - naive datetime assumed to be UTC
                                        except (ValueError, AttributeError):
                                            # Can't parse either, include to be safe
                                            logging.warning(f"Could not parse any timestamps for {table_name} record ID {record.get('id') or record.get('ID')}, including it to be safe")
                                            filtered_records.append(record)
                                            continue
                                    
                                    if created_ts:
                                        try:
                                            comparison_result = created_ts >= ts_dt
                                            logging.debug(f"[SYNC DEBUG] Record {record_id}: Comparing fallback created_at {created_ts} >= {ts_dt} = {comparison_result}")
                                            if comparison_result:
                                                logging.debug(f"[SYNC DEBUG] Record {record_id}: INCLUDED (fallback created_at >= last_sync)")
                                                filtered_records.append(record)
                                            else:
                                                logging.debug(f"[SYNC DEBUG] Record {record_id}: EXCLUDED (fallback created_at < last_sync)")
                                        except (TypeError, ValueError) as compare_error:
                                            # Comparison failed - include record to be safe
                                            logging.warning(f"Timestamp comparison failed for {table_name} record ID {record_id}: {compare_error}, including record")
                                            filtered_records.append(record)
                                else:
                                    # Record has neither edited_at nor created_at that could be parsed
                                    # Include it to be safe (shouldn't happen with proper schema)
                                    logging.warning(f"[SYNC DEBUG] Record {record_id}: No valid timestamps found, including record to be safe")
                                    filtered_records.append(record)
                            
                            all_records = filtered_records
                            logging.info(f"Filtered {table_name} records: {len(all_records)}/{records_checked} records match (modified/created after {timestamp})")
                            logging.debug(f"  - Records with edited_at: {records_with_edited_at}")
                            logging.debug(f"  - Records with created_at only: {records_with_created_at_only}")
                            logging.debug(f"[SYNC DEBUG] Filtering complete: {len(filtered_records)} records passed the filter out of {records_checked} checked")
                            if len(filtered_records) > 0:
                                logging.debug(f"[SYNC DEBUG] Sample of included records: {[r.get('id') or r.get('ID') for r in filtered_records[:5]]}")
                    except Exception as filter_error:
                        logging.error(f"Error filtering {table_name} records by timestamp {timestamp}: {filter_error}", exc_info=True)
                        # If filtering fails, return all records (safer than returning nothing)
                        logging.warning(f"Returning all {len(all_records)} {table_name} records due to filter error")
                else:
                    # timestamp is None - return all records without filtering
                    logging.info(f"No timestamp provided - returning all {len(all_records)} {table_name} records (no filtering applied)")
            
            # Map to SQLite format
            mapped_records = []
            for record in all_records:
                try:
                    mapped_records.append(self._map_to_sqlite_format(record))
                except Exception as map_error:
                    logging.warning(f"Error mapping {table_name} record to SQLite format: {map_error}")
                    # Continue with other records
            
            logging.info(f"Successfully fetched {len(mapped_records)} {table_name} records since {timestamp or 'beginning'}")
            return mapped_records
            
        except Exception as e:
            logging.error(f"Error fetching changes from {table_name} since {timestamp}: {e}", exc_info=True)
            return []
    
    def get_soft_deletions_since(self, table_name: str, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get soft-deleted records from a table since a given timestamp."""
        if not self.client:
            return []
        try:
            query = self.client.table(table_name).select('id, deleted_at')
            # Only get records with deleted_at set
            query = query.not_.is_('deleted_at', 'null')
            if timestamp:
                query = query.gte('deleted_at', timestamp)
            response = query.execute()
            return [self._map_to_sqlite_format(item) for item in response.data]
        except Exception as e:
            logging.error(f"Error fetching soft deletions from {table_name} since {timestamp}: {e}")
            return []
    
    def get_all_ids(self, table_name: str) -> List[int]:
        """Get all record IDs from a table (excluding soft-deleted). Used for full comparison."""
        if not self.client:
            return []
        try:
            all_ids = []
            page_size = 1000
            offset = 0
            
            # Tags use tag_id instead of id
            id_column = 'tag_id' if table_name == 'tags' else 'id'
            
            while True:
                query = self.client.table(table_name).select(id_column)
                # Only filter by deleted_at for words and texts
                if table_name in ['words', 'texts']:
                    query = query.is_('deleted_at', 'null')
                query = query.range(offset, offset + page_size - 1)
                response = query.execute()
                
                if not response.data:
                    break
                all_ids.extend([item.get(id_column) for item in response.data if item.get(id_column)])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_ids
        except Exception as e:
            logging.error(f"Error fetching all IDs from {table_name}: {e}")
            return []
    
    def get_all_tag_ids(self) -> List[int]:
        """Get all tag IDs from Supabase."""
        return self.get_all_ids('tags')
    
    def get_all_word_tag_pairs(self) -> List[tuple]:
        """Get all (word_id, tag_id) pairs from Supabase."""
        if not self.client:
            return []
        try:
            all_pairs = []
            page_size = 1000
            offset = 0
            
            while True:
                response = self.client.table('word_tags').select('word_id, tag_id').range(offset, offset + page_size - 1).execute()
                if not response.data:
                    break
                all_pairs.extend([(item.get('word_id'), item.get('tag_id')) for item in response.data])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_pairs
        except Exception as e:
            logging.error(f"Error fetching all word_tag pairs: {e}")
            return []
    
    def cleanup_old_soft_deletes(self, table_name: str, days_old: int = 30) -> int:
        """Hard delete old soft-deleted records that are older than specified days.
        
        Uses batch deletion for efficiency. Records are permanently removed from the database
        after the grace period to prevent database bloat.
        
        Args:
            table_name: Name of the table ('words' or 'texts')
            days_old: Number of days after which soft-deleted records should be permanently deleted (default: 30)
        
        Returns:
            Number of records permanently deleted
        """
        if not self.client:
            return 0
        
        try:
            from datetime import datetime, timedelta
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=days_old)
            cutoff_iso = cutoff_date.isoformat()
            
            logging.debug(f"Cleanup: Looking for {table_name} records with deleted_at < {cutoff_iso} (older than {days_old} days)")
            
            # First, get the IDs of records that should be deleted
            # This helps us verify what will be deleted and count them
            try:
                response = self.client.table(table_name).select('id, deleted_at').not_.is_('deleted_at', 'null').lt('deleted_at', cutoff_iso).execute()
                
                if not response.data or len(response.data) == 0:
                    logging.debug(f"No old soft-deleted records found in {table_name}")
                    return 0
                
                old_deleted_ids = [item['id'] for item in response.data]
                logging.info(f"Found {len(old_deleted_ids)} old soft-deleted records in {table_name} to clean up")
                
                # Log some examples for debugging
                if len(old_deleted_ids) > 0:
                    sample_ids = old_deleted_ids[:3]
                    logging.debug(f"Sample IDs to delete: {sample_ids}")
                
            except Exception as e:
                logging.error(f"Error fetching old soft-deleted records from {table_name}: {e}")
                return 0
            
            # Use batch deletion: delete all records matching the criteria
            # Note: Supabase delete() doesn't support .select(), so we verify after deletion
            try:
                # Delete all old soft-deleted records in one operation
                # Execute delete without .select() - Supabase doesn't support it on delete()
                self.client.table(table_name).delete().not_.is_('deleted_at', 'null').lt('deleted_at', cutoff_iso).execute()
                
                # Verify deletion by checking if records still exist
                verify_response = self.client.table(table_name).select('id').not_.is_('deleted_at', 'null').lt('deleted_at', cutoff_iso).execute()
                remaining = len(verify_response.data) if verify_response.data else 0
                deleted_count = len(old_deleted_ids) - remaining
                
                if deleted_count > 0:
                    logging.info(f"Cleaned up {deleted_count} old soft-deleted records from {table_name} (older than {days_old} days)")
                else:
                    logging.warning(f"Cleanup query executed but no records were deleted from {table_name}. Expected {len(old_deleted_ids)} records, {remaining} still remain.")
                
                return deleted_count
            except Exception as e:
                # If batch delete fails, try individual deletes as fallback
                logging.warning(f"Batch delete failed for {table_name}, trying individual deletes: {e}")
                return self._cleanup_old_soft_deletes_individual(table_name, cutoff_iso)
                
        except Exception as e:
            logging.error(f"Error cleaning up old soft-deletes from {table_name}: {e}", exc_info=True)
            return 0
    
    def get_old_soft_deletes_count(self, table_name: str, days_old: int = 30) -> int:
        """Get count of old soft-deleted records that would be cleaned up.
        
        Useful for debugging and testing.
        
        Args:
            table_name: Name of the table ('words' or 'texts')
            days_old: Number of days after which records would be deleted
        
        Returns:
            Number of records that would be deleted
        """
        if not self.client:
            return 0
        
        try:
            from datetime import datetime, timedelta
            cutoff_date = datetime.now() - timedelta(days=days_old)
            cutoff_iso = cutoff_date.isoformat()
            
            response = self.client.table(table_name).select('id, deleted_at').not_.is_('deleted_at', 'null').lt('deleted_at', cutoff_iso).execute()
            
            if response.data:
                return len(response.data)
            return 0
        except Exception as e:
            logging.error(f"Error counting old soft-deletes from {table_name}: {e}")
            return 0
    
    def _cleanup_old_soft_deletes_individual(self, table_name: str, cutoff_iso: str) -> int:
        """Fallback method: delete old soft-deleted records one by one."""
        try:
            # Get IDs of old soft-deleted records
            response = self.client.table(table_name).select('id').not_.is_('deleted_at', 'null').lt('deleted_at', cutoff_iso).execute()
            
            if not response.data:
                return 0
            
            old_deleted_ids = [item['id'] for item in response.data]
            logging.info(f"Deleting {len(old_deleted_ids)} records individually from {table_name}")
            
            # Hard delete these records individually
            deleted_count = 0
            for record_id in old_deleted_ids:
                try:
                    # Hard delete (actually remove from database)
                    # Note: delete() doesn't support .select(), so we just execute and check for errors
                    delete_response = self.client.table(table_name).delete().eq('id', record_id).execute()
                    # If no exception was raised, deletion was successful
                    # We can verify by checking if the record still exists
                    verify_response = self.client.table(table_name).select('id').eq('id', record_id).execute()
                    if not verify_response.data or len(verify_response.data) == 0:
                        deleted_count += 1
                    else:
                        logging.warning(f"Record {record_id} from {table_name} still exists after delete attempt")
                except Exception as e:
                    logging.warning(f"Error hard-deleting {table_name} record {record_id}: {e}")
            
            if deleted_count > 0:
                logging.info(f"Individually deleted {deleted_count} records from {table_name}")
            
            return deleted_count
        except Exception as e:
            logging.error(f"Error in individual cleanup: {e}", exc_info=True)
            return 0
    
    def subscribe_to_words(self, callback):
        """Subscribe to real-time changes in words table."""
        if not self.client:
            return None
        
        def handle_change(payload):
            if payload.event_type in ['INSERT', 'UPDATE', 'DELETE']:
                callback(payload)
        
        try:
            return self.client.table('words').on('*', handle_change).subscribe()
        except Exception as e:
            logging.error(f"Error subscribing to words changes: {e}")
            return None
    
    def subscribe_to_texts(self, callback):
        """Subscribe to real-time changes in texts table."""
        if not self.client:
            return None
        
        def handle_change(payload):
            if payload.event_type in ['INSERT', 'UPDATE', 'DELETE']:
                callback(payload)
        
        try:
            return self.client.table('texts').on('*', handle_change).subscribe()
        except Exception as e:
            logging.error(f"Error subscribing to texts changes: {e}")
            return None
    
    def get_all_soft_deleted_items(self, table_name: str) -> List[Dict[str, Any]]:
        """Get all soft-deleted items from a table (where deleted_at IS NOT NULL).
        
        Args:
            table_name: Name of the table ('words' or 'texts')
        
        Returns:
            List of soft-deleted items with all their data
        """
        if not self.client:
            return []
        try:
            all_items = []
            page_size = 1000
            offset = 0
            
            while True:
                # Get all records where deleted_at is not null
                response = self.client.table(table_name).select('*').not_.is_('deleted_at', 'null').order('deleted_at', desc=True).range(offset, offset + page_size - 1).execute()
                if not response.data:
                    break
                all_items.extend([self._map_to_sqlite_format(item) for item in response.data])
                if len(response.data) < page_size:
                    break
                offset += page_size
            
            return all_items
        except Exception as e:
            logging.error(f"Error fetching all soft-deleted items from {table_name}: {e}")
            return []
    
    def restore_word(self, word_id: int) -> bool:
        """Restore a soft-deleted word by setting deleted_at to NULL.
        
        Args:
            word_id: ID of the word to restore
        
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            return False
        try:
            # Set deleted_at to NULL to restore the word
            self.client.table('words').update({'deleted_at': None}).eq('id', word_id).execute()
            logging.info(f"Restored word {word_id} from soft delete")
            return True
        except Exception as e:
            logging.error(f"Error restoring word {word_id}: {e}")
            return False
    
    def restore_text(self, text_id: int) -> bool:
        """Restore a soft-deleted text by setting deleted_at to NULL.
        
        Args:
            text_id: ID of the text to restore
        
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            return False
        try:
            # Set deleted_at to NULL to restore the text
            self.client.table('texts').update({'deleted_at': None}).eq('id', text_id).execute()
            logging.info(f"Restored text {text_id} from soft delete")
            return True
        except Exception as e:
            logging.error(f"Error restoring text {text_id}: {e}")
            return False
    
    def hard_delete_word(self, word_id: int) -> bool:
        """Permanently delete a word from Supabase (hard delete, bypasses grace period).
        
        Args:
            word_id: ID of the word to permanently delete
        
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            return False
        try:
            # Hard delete (actually remove from database)
            self.client.table('words').delete().eq('id', word_id).execute()
            logging.info(f"Permanently deleted word {word_id}")
            return True
        except Exception as e:
            logging.error(f"Error permanently deleting word {word_id}: {e}")
            return False
    
    def hard_delete_text(self, text_id: int) -> bool:
        """Permanently delete a text from Supabase (hard delete, bypasses grace period).
        
        Args:
            text_id: ID of the text to permanently delete
        
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            return False
        try:
            # Hard delete (actually remove from database)
            self.client.table('texts').delete().eq('id', text_id).execute()
            logging.info(f"Permanently deleted text {text_id}")
            return True
        except Exception as e:
            logging.error(f"Error permanently deleting text {text_id}: {e}")
            return False

    def get_auth(self):
        """Return the underlying gotrue auth client, or None if not configured."""
        return self.client.auth if self.client is not None else None


# ---------------------------------------------------------------------------
# Process-wide shared client.
# ---------------------------------------------------------------------------
# All cloud consumers (SyncManager, every DatabaseAdapter, the settings
# connection test) go through this one instance, so a single set_auth_token() or
# reconfigure() applies the user's identity everywhere at once — no code path can
# end up using a tokenless client while another is authenticated.
_shared_client: Optional['SupabaseClient'] = None
_shared_lock = threading.Lock()


def get_supabase() -> 'SupabaseClient':
    """Return the process-wide shared SupabaseClient (created on first use)."""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = SupabaseClient()
        return _shared_client

