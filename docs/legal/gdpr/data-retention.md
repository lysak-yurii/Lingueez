# Data Retention Schedule

*Internal document. Mirrors the application's actual behaviour.*

**Last reviewed:** 2026-06-23

| Data | Where | Retention | Mechanism |
|------|-------|-----------|-----------|
| Account (email, UUID, name) | Cloud (Supabase) | Until account deletion | Hard-deleted on "Delete account" |
| Synced content (words, texts, tags) | Cloud (Supabase) | Until deletion **+ ~30-day grace** | Soft-delete (`deleted_at`) → hard delete after `cleanup_grace_period_days` (default 30) |
| Password hash | Cloud (auth) | Until account deletion | Managed by auth provider |
| Auth tokens | Device (OS keychain or encrypted file) | Until sign-out / "Remove account" | Cleared on sign-out |
| Review/listen history | Device (local SQLite) | Until user deletes local data | Never uploaded |
| App & crash logs | Device | Rotating, capped (~few MB) | `RotatingFileHandler`; redacted on write |
| Local DB backups | Device | Rolling: recent daily + monthly snapshots | Auto-pruned |
| Translation/AI/TTS payloads | Third parties | Per that provider's policy | Not retained by us |
| Update-check queries | GitHub | n/a (no personal data) | — |

### Deletion paths exposed to the user
- **Delete account** → permanent removal of account + all synced content from cloud (local copy
  archived to backups).
- **Remove account** → sign out on this device; cloud data untouched.
- **Export** → user obtains a full copy (portability) before deleting.

### Action items
- ☐ Confirm server-side cascade: deleting an account removes **all** rows
  (`words`, `texts`, `tags`, `word_tags`) for that user id.
- ☐ Confirm Supabase backup/PITR retention window and document it here.
