# OBE / CO-PO data model (design for approval, nothing built yet)

Status: DRAFT 2026-11-09. Pilot scope item 5: internal marks with automatic CO-PO attainment,
questions tagged with CO + Bloom's level. Per CLAUDE.md, build starts only after the acceptance
checks at the bottom are agreed.

Vocabulary (avoids a clash with the base repo, where `Course` means a degree programme):
**Programme** = degree (B.Tech CSE). **Subject** = a paper. **CO** = course outcome of a subject.
**PO / PSO** = programme (specific) outcome.

## 1. What this needs from pilot item 1 (institution setup)

The base has only flat `Course`, `Session`, `Subject`. OBE needs these first (new app `academics`,
tenant schema). Minimal fields only; item 1 will extend them.

| Model | Key fields |
|---|---|
| Department | name, code |
| Programme | department, name, code, duration_semesters |
| Regulation | programme, name (R2022), effective_from; `rules` link (see 4) |
| Batch | programme, regulation, start_year (admission batch) |
| Section | batch, name |
| Subject | programme, regulation, semester, code, name, credits, kind (theory/lab) |
| SubjectOffering | subject, batch/section, academic term, faculty (Staff) |
| Enrolment | offering, student (unique together) |

The existing `Subject.staff` (one fixed teacher) moves to `SubjectOffering.faculty`.

## 2. OBE models (new app `obe`, tenant schema)

```
Programme 1--n ProgrammeOutcome (PO1..PO12, PSO1..)
Subject   1--n CourseOutcome (CO1..)  n--n ProgrammeOutcome   via CoPoMap(strength)
BloomLevel (L1..L6, seeded per college)
SubjectOffering 1--n Assessment 1--n AssessmentQuestion n--n CourseOutcome   via QuestionCO(share)
AssessmentQuestion n--1 BloomLevel
Enrolment 1--n StudentQuestionMark n--1 AssessmentQuestion
Results (snapshots): COAttainment, POAttainment
```

| Model | Fields and constraints |
|---|---|
| BloomLevel | code (L1..L6), name (Remember, Understand, Apply, Analyse, Evaluate, Create). Editable, seeded when a college is created. |
| ProgrammeOutcome | programme, kind (PO/PSO), code, title, description. unique(programme, code). POs are per programme, not hard-coded to NBA's 12. |
| CourseOutcome | subject, code (CO1), description, bloom_level (target level of the outcome). unique(subject, code). |
| CoPoMap | course_outcome, programme_outcome, strength (0-3, max from config). unique pair. CO and PO must belong to the same programme. |
| Assessment | offering, name (Internal 1), kind (test, assignment, quiz, lab), max_marks, date, status (draft, published, locked). |
| AssessmentQuestion | assessment, label (1a), max_marks, bloom_level, order. Optional questions: `choice_group` (answer any n of m). |
| QuestionCO | question, course_outcome, share (percent; rows per question sum to 100; one CO = 100). Needs CO's subject = assessment's subject. |
| StudentQuestionMark | question, enrolment, marks (nullable), absent (bool). unique(question, enrolment). 0 <= marks <= question.max_marks. |
| COAttainment | offering, course_outcome, rule_version, students_counted, students_attained, percent_attained, level (0-3), computed_at, inputs_hash. Snapshot kept for accreditation evidence. |
| POAttainment | programme, batch, programme_outcome, rule_version, direct_level, indirect_level (nullable), final_level, computed_at. |

Indirect attainment (course-exit and graduate surveys) is out of the pilot, but `POAttainment.indirect_level`
and the 80:20 weighting are modelled so it can be added without a migration of results.

## 3. Calculation (all parameters come from rules, section 4)

1. Per student, per CO, per offering: `score% = sum(marks x share) / sum(max_marks x share) x 100`
   over all included questions tagged to that CO. Absent students: excluded from that
   assessment's questions (default) or counted as 0 (rule).
2. CO attainment: `percent_attained` = share of counted students with `score% >= student_threshold`
   (default 60). `level` from the bands (default >=70% students -> 3, >=60 -> 2, >=50 -> 1, else 0).
3. PO attainment for an offering: `sum(level_CO x strength) / sum(strength)` over COs mapped to that PO.
   Programme level for a batch: average (or credit-weighted, by rule) over the subjects mapped to the PO.
4. Final PO level = `direct_weight x direct + indirect_weight x indirect` (default 80:20; pilot has no
   indirect, so final = direct). Compared with the target (default 2.0) to flag shortfalls.

Worked example (this becomes the golden test). CO1 scores of 5 students: 80, 70, 55, 40, 65 with
threshold 60: 3 of 5 attain, 60% of students, level 2. Say CO2 level 3 and CO3 level 1, mapped to PO1 with
strengths 3, 2, 1: `(2x3 + 3x2 + 1x1) / (3+2+1) = 13/6 = 2.17`.

## 4. Rules as configuration (non-negotiable: never hard-coded)

`AttainmentRuleSet` (one per regulation, versioned, effective-dated): student_threshold_pct, level_bands,
absent_policy, direct_weight, indirect_weight, po_aggregation (average or credit-weighted),
po_target, mapping_scale_max. Stored as validated fields, not free-form code. Every result stores
the `rule_version` used, so a past report can be re-derived. The default set is copied into a
college when it is created (tenant isolation: no shared reference data in the public schema).
The defaults above are common NBA/NAAC practice and must be confirmed with the pilot college's IQAC.

## 5. Roles and rules

| Role | Can |
|---|---|
| Faculty | Define assessments and questions, tag CO + Bloom's, enter marks, for own offerings only. |
| HoD | Define COs, POs, CO-PO map for the department; approve; lock assessments. |
| IQAC coordinator | Edit rule sets; view all attainment; export reports. |
| Principal / Management | Read-only dashboards. |
| Student | Own CO levels only (later). |

- A locked assessment's marks change only with a reason; every change to marks, tags, maps and rules goes
  to the audit log (non-negotiable; the log itself is a separate build and a dependency, see open questions).
- Changing a CO-PO map, tags, marks or rules marks the affected results stale; recompute is automatic
  and the snapshot is replaced, with the old one kept in the audit trail.

## 6. Data entry and speed

- Faculty enter marks per question in a grid (students x questions) or import one Excel sheet per
  assessment (same importer as pilot item 2). Total-only marks entry is allowed but gives no CO split.
- Targets: attainment for one offering (about 60 students, 20 questions) recomputes in under 1 second;
  done with database aggregation, not Python loops.

## 7. Acceptance checks (to agree before building)

1. Golden example above gives exactly 60% / level 2 / PO 2.17.
2. Changing `student_threshold_pct`, the bands or the weights changes results with no code change.
3. A question tagged to two COs with shares 60/40 splits its marks 60/40 in each CO's score.
4. An absent student is excluded (default) or zero (rule) exactly as configured.
5. CO and PO from different programmes cannot be mapped; question and CO from different subjects cannot be tagged;
   marks above the question maximum are rejected.
6. A faculty member cannot read or edit another faculty member's offering; HoD from another department cannot edit this one's COs.
7. Tenant isolation: College A's outcomes, maps, marks and results are never visible in College B (new tests added to `colleges/tests/`).
8. Editing a mark after locking requires a reason and writes an audit entry; stale results recompute and the snapshot records the rule version.
9. Full attainment recompute for 60 students x 20 questions runs in under 1 second on the dev database.
10. Excel export of the CO attainment table and the CO-PO matrix matches the on-screen numbers.

## 8. Open questions for the pilot college

1. Which accreditation comes first: NBA (engineering, 12 POs plus PSOs), NAAC, or both? Affects PO lists and report formats.
2. Are the default thresholds and 80:20 weighting acceptable, or does the university prescribe its own?
3. Pilot scope excludes exams/results, but NBA attainment normally includes the university exam. Is internal-only fine for the pilot, or should a single "university exam" assessment (total only, tagged by CO split) be allowed?
4. Do faculty have question-wise marks (needed for CO split) or only totals?
5. Audit log: build it before OBE (recommended, since marks edits need it) or in parallel.

## 9. Suggested build order

1. `academics` minimum models and admin screens (item 1), with the `user_type` fix.
2. Audit log.
3. `obe` models, rule set and the calculation as a pure, tested function (golden test first).
4. Marks entry grid and Excel import.
5. Attainment screens and exports.
