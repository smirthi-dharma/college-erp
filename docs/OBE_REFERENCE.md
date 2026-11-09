# OBE / CO-PO Reference — Official Rules for the ERP

Compiled 9 November 2026 from official NBA, AICTE and (where available) NAAC sources.
Put this file in `docs/OBE_REFERENCE.md`. It is the source of truth for the CO-PO module; the
companion file `obe_seed.json` holds the same reference data in machine-readable form.

**Rule for the code:** anything marked **PRESCRIBED** must be built exactly as stated.
Anything marked **NOT PRESCRIBED** must be a per-college, per-regulation *configuration*,
never a hard-coded constant.

---

## 1. Sources used

| # | Document | Publisher | Date | Link |
|---|---|---|---|---|
| S1 | Manual for Accreditation of UG Engineering Programs (Tier I), 2026 Edition, incl. SAR format (GAPC V4.0, July 2024) | NBA | 2026 | https://www.nbaind.org/files/Manual%20for%20UG%20Engineering%20Tier%20I-%202026%20Edition_Format_20260521165316.pdf |
| S2 | SAR format, UG Engineering, Tier II | NBA | 14 Jan 2025 | https://www.nbaind.org/files/2-SAR-UG-EG-T2-14-1-2025_Format_20250115122255.pdf |
| S3 | Examination Reform Policy | AICTE | Nov 2018 | https://aicte.gov.in/sites/default/files/ExaminationReforms.pdf |
| S4 | NAAC framework status (secondary reports; NAAC's own binary/MBGL manuals not yet published) | Various | 2025–26 | see section 4 |

Tier I = autonomous institutions, universities, deemed universities, INIs (S1 §1.4).
Tier II = affiliated, non-autonomous colleges. Most pilot colleges in Tamil Nadu will be Tier II.

---

## 2. NBA (UG Engineering)

### 2.1 Framework versions — PRESCRIBED
- NBA revised the SAR in 2013 under GAPC V3.0 with **12 POs**, and in 2024 under **GAPC V4.0 with 11 POs** (S1 §3.1).
- **Design impact:** PO sets must be **versioned**. Older batches may have been mapped to the 12-PO set;
  new mappings use the 11-PO set. Never overwrite one set with the other.

### 2.2 Program Outcomes (GAPC V4.0, 11 POs) — PRESCRIBED
Full statements are in `obe_seed.json` (`po_sets.NBA_UG_ENGG_GAPC4`). Titles (S1 §3.4, S2 Annexure II):

| Code | Title | Knowledge profile refs |
|---|---|---|
| PO1 | Engineering Knowledge | WK1–WK4 |
| PO2 | Problem Analysis | WK1–WK4 |
| PO3 | Design/Development of Solutions | WK5 |
| PO4 | Conduct Investigations of Complex Problems | WK8 |
| PO5 | Engineering Tool Usage | WK2, WK6 |
| PO6 | The Engineer and The World | WK1, WK5, WK7 |
| PO7 | Ethics | WK9 |
| PO8 | Individual and Collaborative Team work | — |
| PO9 | Communication | — |
| PO10 | Project Management and Finance | — |
| PO11 | Life-Long Learning | WK8 |

- POs "should remain unchanged as prescribed by NBA" (S1 §3.5.1.1) → **POs are read-only for colleges**.
- NBA defines distinct PO sets per discipline (S1 §3.4). PG, Diploma, MBA, MCA, Pharmacy have their own sets — add them as separate versioned sets when needed.
- The legacy 12-PO set (GAPC V3.0) is in `obe_seed.json` (`po_sets.NBA_UG_ENGG_GAPC3`), taken from S3.

### 2.3 PSOs — PRESCRIBED (limits differ by tier)
- Tier I: program shall specify **2–4 PSOs, if required**; they are program-specific and "beyond POs" (S1 §3.4).
- Tier II SAR: "PSOs up to 2–3" (S2 Annexure II).
- **Design:** PSOs are editable per programme; allow 0–4, warn above the tier's limit.

### 2.4 Course Outcomes — PRESCRIBED
- "The maximum number of outcomes for a course is expected to be around 6" (S2 §1.4.1). → warn above 6, don't block.
- COs are stated semester-wise and aligned to POs/PSOs (S1 §3.5.1.1).

### 2.5 Correlation levels (course articulation matrix) — PRESCRIBED
| Value | Meaning |
|---|---|
| 1 | Slight (Low) |
| 2 | Moderate (Medium) |
| 3 | Substantial (High) |
| – | No correlation (store as NULL, never 0) |

Source: S2 Table 1.4.2.1 notes.

### 2.6 Required matrices / tables — PRESCRIBED formats (S2)
1. **Course outcomes** per course: CO number + statement (Table 1.4.1.1).
2. **Course articulation matrix**: rows CO-1…CO-n, columns PO-1…PO-11 + PSOs, cells 1/2/3/– (Table 1.4.2.1).
3. **Program articulation matrix**: rows = courses (code, name), columns = POs + PSOs (Table 1.5.1).
4. **PO/PSO attainment, direct**: rows = courses C101…C409, final row "Direct Attainment" (Table 3.8.1).
   Course code convention: first digit = year of study, next two = course number (C101 = year 1, course 01).
5. **PO/PSO attainment, indirect**: rows = surveys, final row "Indirect Attainment" (Table 3.8.2).
6. **Overall PO/PSO attainment**: rows Direct / Indirect / Overall (Table 3.8.3).

The ERP's NBA export must reproduce these six tables exactly.

### 2.7 How attainment must be measured
**PRESCRIBED**
- Program shall **set CO attainment levels for each course**; CO attainment measured through **CIE and SEE** must be detailed (S2 §3.7.2).
- CO target "may be stated in terms of **percentage of students getting more than class average marks** or **set by the program**" in each assessment instrument mapped to the COs (S2 §3.7.2).
  → The target rule needs **two modes**: `class_average` and `fixed_threshold`.
- Assessment tools include SEE, mid-semester tests, lab exams, student portfolios (S2 §3.7.1);
  labs, internships and projects are assessed with **rubrics** linked to COs/POs (S2 §3.3–3.5).
  → Assessments must support **rubric scoring**, not only question-wise marks.
- **Direct PO/PSO attainment = average across all courses addressing that PO/PSO** (S2 note to Table 3.8.1).
- **Indirect PO/PSO attainment** is based on student exit surveys, employer surveys, etc.; record survey type and source (S2 notes to Table 3.8.2).
- PO/PSO attainment is measured "by aggregating the CO attainment across courses" (S1 §3.5.1.3, §3.5.1.8).

**NOT PRESCRIBED (must be configuration)** — NBA does not fix any of these:
- The marks threshold in `fixed_threshold` mode (commonly 50–60%).
- Attainment level bands (e.g. level 3 if ≥70% of students reach the target).
- CIE : SEE weighting inside CO attainment.
- How a course's CO levels become that course's PO value in Table 3.8.1 (common practice: correlation-weighted average of CO levels; others use simple average). Store the chosen formula with the result.
- Direct : indirect weighting for overall attainment (common practice 80:20 or 90:10).
- PO target level, absent-student handling, rounding.

### 2.8 Criterion 3 structure and marks — Tier II SAR (S2)
| Item | Marks |
|---|---|
| 3.1 Evaluation of continuous assessment (CO alignment of questions) | 10 |
| 3.2 Evaluation of SEE question paper | 10 |
| 3.3 Lab and workshop evaluation (rubrics) | 10 |
| 3.4 Industrial training / internship evaluation (rubrics) | 10 |
| 3.5 Evaluation of projects (rubrics) | 20 |
| 3.6 Evidence of addressing SDGs | 10 |
| 3.7 Attainment of COs (3.7.1 tools: 5; 3.7.2 records vs set levels: 20) | 25 |
| 3.8 Attainment of POs and PSOs | 25 |
| **Total** | **120** |

Related: Criterion 1.4 Course outcomes and course articulation matrix (25), 1.5 Program articulation matrix (10).
Tier I totals by criterion: 1–4 = 120 each, 5 = 100, 6 = 120, 7 = 100, 8 = 80, 9 = 120; total 1,000 (S1 Table 1).

**Evidence:** "Details to be kept in course files for evaluation" (S2 §3.1–3.5). → Each course needs a
**course file** record that can hold question papers, answer keys, rubrics and sample scripts.

### 2.9 Assessment years — PRESCRIBED terminology (S2)
- CAY = Current Academic Year
- CAYm1 = CAY minus 1 = Current Assessment Year
- CAYm2 = CAY minus 2 = Current Assessment Year minus 1

Reports must be generatable for any of these three windows.

---

## 3. AICTE Examination Reform Policy (S3)

### 3.1 Bloom's levels (revised taxonomy) — use as the standard
| Level | Name | Descriptor |
|---|---|---|
| L1 | Remembering | Recalling previously learned material |
| L2 | Understanding | Explaining ideas or concepts |
| L3 | Applying | Using information in another familiar situation |
| L4 | Analysing | Breaking information into parts to explore relationships |
| L5 | Evaluating | Justifying a decision or course of action |
| L6 | Creating | Generating new ideas, products or ways of viewing things |

Action verbs per level are in `obe_seed.json` (`bloom_levels[].verbs`). Some verbs appear at several
levels; the policy says context decides the level (S3 §3.2). → Verbs are a **suggestion** in the UI, not a validator.

### 3.2 Question paper format
Model papers tag every question with: **Q.No, Question, Marks, CO, BL, PO, PI code** (S3 Appendix C).
→ Each question record: marks, one or more COs, Bloom level, optional POs, optional PI codes.

### 3.3 Competencies and Performance Indicators (PIs)
Two-step method: for each PO define competencies, then measurable PIs (e.g. PI 1.4.1);
exam questions are mapped to PIs (S3 §2.2). Treat PIs as **optional** in the pilot; support the field.

### 3.4 Assessment planning guidance
- CIE and SEE normally test L1–L3 and some L4; L4–L6 are better assessed through projects,
  internships and course work (S3 §3.3).
- Institutions should set an **upper limit for lower-order questions**; the policy gives
  "no more than 40% weightage for knowledge-oriented questions" as an example, and notes the split can vary by course (S3 §3.3).
  → Configurable warning on the paper builder: share of marks at lower-order levels above the college's cap
  (the college chooses which levels count, e.g. L1 only or L1–L2).

---

## 4. NAAC

- In the Revised Accreditation Framework (RAF) used by colleges until now, CO-PO work is reported under
  **Key Indicator 2.6**: **2.6.1** (POs and COs stated, displayed and communicated) and **2.6.2**
  (attainment of POs, PSOs and COs evaluated by the institution). These codes are taken from colleges'
  published SSR/AQAR documents; NAAC's own manual could not be fetched in this research.
- NAAC announced **Binary Accreditation** (Accredited / Not Accredited) plus optional
  **Maturity-Based Graded Levels (MBGL, Levels 1–5)** on 10 Feb 2025. Secondary sources report that the
  Binary portal and final manuals were still not operational as of mid-2026.
- NAAC does not prescribe a specific CO/PO attainment formula. Unlike NBA, NAAC POs for non-engineering
  programmes are defined by the college or its university.

**Design impact:** build one attainment engine, and keep NAAC reporting as a separate, replaceable
**export layer** (RAF 2.6.1/2.6.2 now; Binary/MBGL when NAAC publishes it). Re-check naac.gov.in before
building the NAAC export.

---

## 5. Changes this implies for `docs/CO_PO_DATA_MODEL.md`

1. **Versioned PO sets** (GAPC3 12 POs, GAPC4 11 POs, others later), read-only to colleges; a programme
   regulation points at one set.
2. **PSOs**: 0–4 per programme, editable, warn above the tier limit.
3. **CO target modes**: `class_average` and `fixed_threshold`, chosen per course.
4. **Rubric assessments** alongside question-wise marks (labs, internships, projects).
5. **Program-level direct PO attainment = simple average across courses addressing the PO** (prescribed);
   the course-level PO formula stays configurable and is stored with every snapshot.
6. **Indirect attainment** from surveys, each with type and source reference.
7. **Overall = direct + indirect** with configurable weights.
8. **Assessment windows** CAY / CAYm1 / CAYm2 on every report.
9. **Course file** with evidence attachments per course offering.
10. **SEE marks import** (university results), since NBA expects CIE and SEE.
11. **Bloom level + optional PI codes** on every question; lower-order cap warning.
12. **NBA export** reproducing Tables 1.4.1.1, 1.4.2.1, 1.5.1, 3.8.1, 3.8.2, 3.8.3.
13. Correlation "–" stored as NULL, never 0, so averages are not dragged down.

---

## 6. Questions for the senior faculty

1. Is the pilot college Tier I (autonomous) or Tier II (affiliated)? This decides PSO limits and SAR format.
2. Which CO target mode does the college use: class average, or a fixed threshold (what %)?
3. What are the college's attainment level bands and PO target?
4. CIE : SEE weighting, and direct : indirect weighting?
5. How does the college convert a course's CO levels into that course's PO values (weighted or simple average)?
6. Which surveys feed indirect attainment (course exit, programme exit, alumni, employer)?
7. Do any current batches still use the 12-PO (GAPC 3.0) mapping?
8. For non-engineering programmes, who supplies the POs (college or university)?
9. Does the college use Performance Indicator codes on question papers?
10. Please share one past course's CO-PO calculation in Excel — it becomes the golden test case.
