# Records of Processing Activities (RoPA)

*Internal document — Article 30 GDPR. Not for public publication, but keep it ready to show a
supervisory authority on request.*

**Controller:** Lingueez, privacy@lingueez.app
**Last reviewed:** 2026-06-23

> Note: a controller with fewer than 250 employees is only strictly obliged to keep Art. 30
> records for non-occasional processing or special-category data. Because the hosted service
> processes account data on an ongoing basis, maintaining this record is recommended.

---

| # | Processing activity | Categories of data subjects | Categories of personal data | Purpose | Legal basis | Recipients / processors | Transfers outside EU/EEA | Retention | Security measures |
|---|---------------------|-----------------------------|------------------------------|---------|-------------|-------------------------|--------------------------|-----------|-------------------|
| 1 | **Account management** | Registered users | Email, account UUID, display name, password hash | Create & authenticate accounts | Art. 6(1)(b) | Supabase (auth) | None (EU region) | Until account deletion | TLS, hashed passwords, RLS |
| 2 | **Account emails** | Registered users | Email address | Email verification, password reset | Art. 6(1)(b) | Supabase (SMTP) / configured SMTP | Depends on SMTP provider | Transient | TLS |
| 3 | **Content sync** | Registered users with sync on | Words, definitions, texts, tags, timestamps, status/favourite | Cloud backup & cross-device sync | Art. 6(1)(b) | Supabase (DB) | None (EU region) | Until deletion + 30-day grace | TLS, RLS |
| 4 | **Optional translation (DeepL)** | Users who configure DeepL | Text submitted for translation | Translate user content | Art. 6(1)(a) | DeepL SE | None (Germany/EU) | Not retained by us | TLS |
| 5 | **Default translation/TTS (free Google)** | Users who use the feature | Text submitted | Translate / speak content at the user's request | Art. 6(1)(b)/(a) | Google (unofficial) | Likely US, no SCC | Not retained by us | TLS; ⚠ no DPA |
| 6 | **Optional AI** | Users who enable AI | Prompts (words, texts) | Generate definitions/texts | Art. 6(1)(a) | OpenAI / Google (Gemini) | US (SCCs/DPF) | Per provider | TLS |
| 7 | **Optional Cloud TTS** | Users who configure it | Text to synthesise | Read-aloud / MP3 export | Art. 6(1)(a) | Google Cloud | US (SCCs/DPF) | Per provider | TLS |
| 8 | **Security/diagnostic logging** | Users (device-local) | Redacted app/crash logs | Troubleshooting, security | Art. 6(1)(f) | None (local) unless user exports | None | Rotated, capped | Local, redacted |
| 9 | **Update check** | All users (if enabled) | None (version query only) | Provide updates | Art. 6(1)(f) | GitHub | US | n/a | TLS |

---

### Notes
- Activities 4–7 are **user-initiated** and most require the user's own API key; for some of
  these the user may be regarded as controller. Documented here for completeness and transparency.
- Activity 5 is the open compliance item — see [subprocessors.md](subprocessors.md).
