# Mateo Medicine RAG — Starter Document Set

**Status: UNREVIEWED DRAFT. NOT a clinically approved formulary.**

These authored summaries cite authoritative public sources but have **not been independently clinician-reviewed**. They are for development, document indexing, retrieval tests and demonstrations, not prescribing or medication selection.

## Included documents

- `01_acne_treatment_overview.txt` — acne therapy classes, context and safety
- `02_benzoyl_peroxide_reference.txt` — ingredient overview and precautions
- `03_retinoids_azelaic_salicylic_reference.txt` — other acne ingredients and pregnancy caution
- `04_contact_dermatitis_and_allergy_safety.txt` — irritation, dermatitis and urgent red flags
- `medicine_catalog_draft.json` — structured illustrative entries; no dosing or brand recommendations

## How to use

Copy these files to your project's `medicine_data/` folder, then run the ingestion command for a TXT document from your project root. Use truthful metadata: **do not claim clinical review until a qualified reviewer has reviewed and approved it**. If the existing ingestion CLI requires `--reviewed-by` and `--reviewed-on`, either wait for actual review, or limit experiments to local non-clinical development and modify your ingestion metadata policy to represent `review_status=unreviewed` correctly.

Example after actual review:

```powershell
python -m backend.medicine_ingestion "medicine_data\02_benzoyl_peroxide_reference.txt" --title "Benzoyl Peroxide Reference" --source-url "https://www.nhs.uk/medicines/benzoyl-peroxide/" --reviewed-by "REAL_REVIEWER_NAME" --reviewed-on "YYYY-MM-DD"
```

**These notes are original summaries, not copies of source webpages.** Verify source URLs, current clinical standards, jurisdictional labeling, ingredient contraindications, and medical accuracy before any use involving patients. Each reference is a source for retrieval, not an instruction to prescribe. No photo should trigger an automatic drug recommendation.
