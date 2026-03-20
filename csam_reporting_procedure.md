# Content Review: Illegal Content Reporting Procedure

This document is for the Foodie Moiety content review process. Follow these steps if illegal or potentially illegal content is encountered during upload review.

---

## CSAM (Child Sexual Abuse Material)

**This is a federal legal obligation. Failure to report carries fines under 18 U.S.C. 2258A.**

### Steps

1. **STOP reviewing immediately.** Do not view the content further than necessary to identify it.

2. **Do NOT delete the upload.** Preserve the S3 object and all associated metadata as evidence. Do not modify, move, or remove anything.

3. **Record the following information:**
   - Upload ID / Book ID / Recipe ID
   - S3 key (the zip file path)
   - Uploader user ID
   - Uploader email address
   - Uploader IP address
   - Date and time of upload
   - Date and time you discovered the content

4. **Report to NCMEC within 24 hours:**
   - Go to [CyberTipline.org](https://www.cybertipline.org)
   - File an Electronic Service Provider (ESP) report
   - Include all information from step 3
   - Save the CyberTipline report confirmation number

5. **Suspend the uploader's account** using the admin tools. Do not notify the user of the reason — this could interfere with any investigation.

6. **Contact law enforcement** if you believe there is an immediate threat to a child's safety. Call the local FBI field office or 911.

7. **Do NOT discuss the report publicly** or with anyone outside of law enforcement and NCMEC.

---

## Copyright Infringement (DMCA)

If content appears to infringe copyright (e.g., copied recipes from a published cookbook with original photos):

1. If reported via a DMCA takedown notice to `dmca@foodiemoiety.com`:
   - Verify the notice includes: identification of the copyrighted work, the infringing material, contact info, good faith statement, and signature
   - Remove or disable access to the content promptly
   - Notify the uploader that their content was removed due to a DMCA notice
   - Preserve the upload metadata and takedown notice for records

2. If discovered during review (no formal notice):
   - Reject the upload with reason "Potential copyright infringement"
   - No obligation to file a formal report, but document the decision

---

## Other Illegal Content

Includes but is not limited to: threats of violence, content promoting terrorism, or other federally prohibited material.

1. **Reject and remove the upload.**
2. **Preserve logs:** uploader IP, user ID, email, timestamp, S3 key.
3. **Suspend the uploader's account.**
4. **Report to law enforcement** if the content involves threats or imminent harm.

---

## Objectionable but Legal Content

Content that violates the Terms of Service but is not illegal (e.g., spam, off-topic content, offensive but legal material):

1. Reject the upload with an appropriate reason.
2. No reporting obligation. This is at the platform owner's discretion.
3. Consider suspending repeat offenders.

---

## Key Contacts

| Resource | Contact |
|---|---|
| NCMEC CyberTipline | [cybertipline.org](https://www.cybertipline.org) |
| FBI Tips | [tips.fbi.gov](https://tips.fbi.gov) |
| DMCA notices | dmca@foodiemoiety.com |
| Content safety reports | safety@foodiemoiety.com |

---

## Legal References

- **18 U.S.C. 2258A** — Mandatory reporting of CSAM by electronic service providers
- **17 U.S.C. 512** — DMCA safe harbor provisions
- **47 U.S.C. 230** — Section 230 platform liability protections
