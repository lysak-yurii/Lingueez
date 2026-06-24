# Sub-processors & Data-Processing Agreements

*Internal/transparency document. Keep current; link from the Privacy Policy.*

**Last reviewed:** 2026-06-23

A "sub-processor" is a third party that processes personal data on the controller's behalf, or
to which personal data is transferred to deliver an optional feature.

## Core (hosted service)

| Provider | Purpose | Personal data shared | Transfer mechanism | DPA in place? |
|----------|---------|----------------------|--------------------|---------------|
| **Supabase Inc.** | Database, auth, account emails | Email, account UUID, name, all synced content | EU region (data residency) | ☐ **TODO: sign Supabase DPA in dashboard** |

## Optional (user-enabled) integrations

| Provider | Purpose | Personal data shared | Transfer mechanism | DPA in place? |
|----------|---------|----------------------|--------------------|---------------|
| **DeepL SE** | Translation (with user API key) | Text submitted | Germany/EU | ☐ TODO: confirm DeepL terms/DPA for your plan |
| **OpenAI** | AI definitions/text (user API key) | Prompts | SCCs / DPF | ☐ TODO: accept OpenAI DPA |
| **Google LLC** (OAuth, Cloud TTS, Gemini) | Sign-in, TTS, AI (user-configured) | Email/profile (OAuth); text (TTS/AI) | SCCs / EU-US DPF | ☐ TODO: Google Cloud DPA |
| **GitHub** | Update check (version only) | None | — | n/a |
| **Wikipedia / user-chosen sites** | Lookups | Search query / URL | varies | user-initiated |

## ⚠️ Open compliance item — unofficial Google endpoints

The **default free translation** (`translate.googleapis.com/translate_a/single`) and the
**default read-aloud** (gTTS, which calls `translate.google.com`) use Google endpoints that are
**not official, public APIs**:

- There is **no data-processing agreement** covering them and **no documented transfer safeguard**.
- Their use likely **violates Google's Terms of Service**.

**Mitigation implemented:** these features run only when the user actively uses them, are
**disclosed** in the Privacy Policy (§3.4, §5), and the user can switch the defaults to the
agreement-backed alternatives (**DeepL**, **Google Cloud TTS**) in Settings.

**Recommended before scale:** switch the *defaults* to official, DPA-backed APIs to remove the
residual ToS/transfer risk entirely. Tracked in the [release checklist](../README.md).
