# Personal-Data Breach Response Plan

*Internal runbook — Articles 33 & 34 GDPR.*

**Last reviewed:** 2026-06-23

A "personal-data breach" is any breach of security leading to accidental or unlawful
destruction, loss, alteration, unauthorised disclosure of, or access to personal data.

## 1. Detect & contain (immediately)
- Identify what happened, which systems (Supabase project, auth, local exports) and which data.
- Contain: rotate keys/credentials, revoke sessions, close the hole, enable extra logging.

## 2. Assess risk
- What data, how many users, sensitivity, likelihood of harm (identity, account takeover, etc.).
- Determine whether the breach is **likely to result in a risk** to individuals' rights.

## 3. Notify the supervisory authority — within **72 hours** (Art. 33)
- If there is a risk to individuals, notify **[STATE DATA-PROTECTION AUTHORITY]** within 72h of
  becoming aware. If later than 72h, include the reason for delay.
- Include: nature of breach, categories & approximate number of users/records affected, likely
  consequences, measures taken/proposed, and DPO/contact point.
- If risk is unlikely, document the reasoning instead (no notification needed, but record it).

## 4. Notify affected users — without undue delay (Art. 34)
- Required if the breach is **likely to result in a HIGH risk** to individuals.
- Plain-language email: what happened, likely consequences, what you're doing, what they should
  do (e.g. change password), and a contact point.
- Not required if data was encrypted/unintelligible, or you've since neutralised the high risk,
  or notifying each user is disproportionate (then make a public communication instead).

## 5. Record (always)
- Log every breach (facts, effects, remedial action) in an internal register, even if not notified.

## Key contacts (fill in)
- Controller / decision-maker: **Lingueez · privacy@lingueez.app**
- Supervisory authority: **[AUTHORITY NAME + online breach-report URL]**
- Supabase support / security contact: **[link]**

## Pre-drafted assets (prepare in advance)
- ☐ Authority notification template
- ☐ User notification email template (EN + UK)
- ☐ Status-page / README notice template
