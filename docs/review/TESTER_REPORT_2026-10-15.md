# Tester-ийн тайлан (зөвхөн уншдаг шалгалт, 2026-10-15)

Tester агент (`.claude/agents/tester.md`) кодыг өөрчлөөгүй. Тайланг энд эх хэлбэрээр нь (англиар) хадгалсан.
Засварын шийдвэр, төлөв: `docs/review/TESTER_FIXES.md`.

## Summary

| Severity | Count |
|---|---|
| HIGH | 7 |
| MEDIUM | 13 |
| LOW | 20 |

The evaluation core works end to end: oracle runs give 100% EX on the final dev/test splits for both model families and
A7 on all draft domains; `check-gold --testsuite` 500 items / 0 errors / 1 warning (intentional A045 alt); split, JSONL
fields, McNemar, seeds and run metadata behave as required. What blocks the plan: the server scripts can't serve
sqrl-9b/qwen3.5-9b (T-01) and `run_plan_l4.sh` dies at its first stage (T-01, T-02); in the default flow, group B/E/F
results use placeholder baselines and the resume logic makes this unrecoverable by re-running (T-03); LLM outages are
saved as final wrong answers (T-04); several training paths are invalid (D2/D3/D4/GRPO data: T-05, T-06; F4 bonus has
no effect: T-07).

## Findings

| id | severity | file:line | problem | why it matters | suggested fix (not applied) |
|---|---|---|---|---|---|
| T-01 | HIGH | config/models.yaml:30,31,44; scripts/server/serve_model.sh:25,44 | `--limit-mm-per-prompt '{"image":0,"video":0}'` is stored with literal single quotes and `$VARGS` is expanded unquoted, so vLLM receives the quotes; `json.loads` fails. | vLLM can't start sqrl-9b or qwen3.5-9b; `run_plan_l4.sh` aborts on its first command. | Remove the quotes or build the argument list as a bash array. |
| T-02 | HIGH | pipeline/router.py:101,220; models.yaml:99-101; run_plan_l4.sh:22-24; eval/runner.py:302-304 | No `router_model` key: `route` falls back to gemma-4-e4b while qwen3.5-9b is served → 404, plan stops; cache name mismatch; runner doesn't check the route cache. | Plan stops in caches stage; F8 silently equals B5. | Add `router_model: qwen3.5-9b`; raise when semantic_router is on and the cache is empty. |
| T-03 | HIGH | run_plan_l4.sh:29-66; eval/runner.py:319-324,361-365; eval/report.py:166,187 | (a) default plan runs A→B→E→F with placeholder vars; E before C/D. (b) resume matches only question_id; meta overwritten with new options. (c) report takes baselines from current vars; no script passes `--overwrite`. | B/E/F silently use the wrong baseline; meta becomes false; re-running keeps the error. | Stop after each stage, move E after C/D; store opts fingerprint and refuse to resume on mismatch; report reads the base from meta. |
| T-04 | HIGH | pipeline/orchestrator.py:427-430; eval/runner.py:323,336-345; eval_adapters.sh:19-21 | Infrastructure failures (vLLM down, OOM, 404 adapter) are written as answers and treated as done on resume; eval_adapters runs IDs whose adapters don't exist. | One vLLM crash permanently lowers EX. | Don't persist LLM/exception records; abort after N consecutive failures; preflight `/v1/models`; run only IDs whose adapters exist. |
| T-05 | HIGH | training/formats.py:128-133; training/grpo.py:114-118; training/data.py:119-122 | Item index globs every `data/train/*.jsonl`; `bird_selected.jsonl` (English only) and `bird_mn.jsonl` share ids, last read wins (arbitrary on ext4). | D2/D3 BIRD prompts with empty question; GRPO drops all mn/latin BIRD rows. | Index only sampled files or prefer non-empty question_mn; assert non-empty questions. |
| T-06 | HIGH | config/training.yaml:37,50; models.yaml:15,27; training/sft.py:54 | B5 prompts (~13.5k tokens) exceed SFT max_length 8192 and GRPO max_prompt_length 12000; TRL truncates. | d2/f10 own-DB rows lose completions; D4 prompts cut; D1 vs D2 and F10 invalid. | Raise limits (16k/15k) or shorten; drop and log over-long rows. |
| T-07 | HIGH | training/grpo.py:91-101 | F4 bonus applied only when all 24 rollouts share one hash, to all of them — constant within each group, zero advantage change. | F4 = C6 + noise. | Per-completion bonus (hash matches other variants' majority). |
| T-08 | MEDIUM | training/grpo.py:120-122,133; train_a100.sh:37 | GRPO pool includes the English sampling stats. | C6 not Cyrillic/Latin GRPO; C7 − C6 doesn't isolate replay. | Exclude `en` unless the recipe asks. |
| T-09 | MEDIUM | config/experiments.yaml:55,57 | B1 and B3 each also turn on knowledge_notes; B6/B8 keep them. | Breaks "one change per run". | Put notes in all B bases or tie them to one run. |
| T-10 | MEDIUM | retrieval/indexes.py:289-291; retriever.py:101-137; orchestrator.py:189-191 | DAIL skeleton re-rank never used (`preliminary_sql` never passed; `exclude_families` unused). | Weaker than described. | Pass a preliminary SQL or document question-similarity only. |
| T-11 | MEDIUM | eval/metrics.py:91-104,140-147 | E3 curve TrustSQL computed on answerable only. | Undervalues abstention; threshold too low. | Compute over all categories with trust_score. |
| T-12 | MEDIUM | eval/metrics.py:107-159; eval/report.py:197-198 | Missing: value recall, table/column recall, F8 routed coverage, GPU-s/question; forgetting runs (F1-<tag>, Belebele) not shown. | Plan notes can't be filled. | Compute them; add a forgetting section. |
| T-13 | MEDIUM | eval/report.py:78-83; eval/judge.py:79 | `<run>.judge.jsonl` next to run files crashes `collect()` (`int("0.judge")`). | `t2s report` crashes after a judge run. | Write judge output elsewhere or restrict globs. |
| T-14 | MEDIUM | security/sql_guard.py:161-185 | As-of rewrite misses `date('now')`, one-arg `age(x)`, implicit `'now'` literals; `SELECT date('now')` returns the real date. | A1 runs not reproducible across days. | Rewrite Date/TsOrDsToDate of 'now'/'today' and one-arg age(). |
| T-15 | MEDIUM | scripts/server/env.sh:3 | `set -euo pipefail` in a file users `source` interactively — first failing command exits the shell. | Manual workflow closes the SSH session. | Move the set options into the scripts. |
| T-16 | MEDIUM | train_a100.sh:19,53-61; experiments.yaml:13-14; training.yaml:69,78; training/sampling.py:49 | GRPO from BEST_SFT=c3 in the same run before C1–C5 are evaluated; best SFT set in four places; d2 hardcodes C3 mix; BEST_A override ignored by training sampling. | "Best C + GRPO" and D2 not based on the measured best. | Separate SFT and GRPO phases with explicit best-SFT; derive settings from experiments vars. |
| T-17 | MEDIUM | bird/runner.py:118-122; training/sampling.py:89-91; docs/TRAINING.md:37 | `bird_prompt` adds evidence only with opts.glossary, so plain BIRD training prompts have no evidence; docs say they do. | C1/C2 BIRD samples without needed hints. | Decide and align code and docs. |
| T-18 | MEDIUM | retriever.py:52-59; embed.py:51-61; runner.py:309-314; SERVER.md:97-98 | Embedder failure silently falls back to BM25; meta lacks index/embedding/adapter/quantization; bge-m3 on the same GPU as vLLM likely fails. | Retrieval quality can differ untraced. | Record index/embedder/adapter in meta; embeddings on CPU at eval or fail loudly. |
| T-19 | MEDIUM | eval/annotate.py:48; retrieval/build.py:90-96 | F9 ids prefixed "F9-" defeat the eval-id exclusion; pool not checked against dev/test. | Eval questions could leak into F9 few-shot pools. | Reject pool records whose id/question is in dev/test. |
| T-20 | MEDIUM | eval/report.py:147-212; cli.py; SERVER.md:85-89 | No command compares two runs (e.g. vs current system) with McNemar. | Final replacement criterion can't be produced. | Add `t2s compare --a --b`. |
| T-21 | LOW | report.py:326-330; orchestrator.py:503-504 | derive-abstain abstains only answered records; pipeline also abstains on failed. | Derived E3 differs from production. | One rule. |
| T-22 | LOW | metrics.py:111,129; default.yaml:116 | executable_pct excludes no-SQL records; p95 measured at concurrency 8. | Inflated executable %, latency bias. | Use all records; document/measure latency at concurrency 1. |
| T-23 | LOW | default.yaml:44-59,72-90,110 | Several config keys never read; eval executor has no EXPLAIN max_cost. | Misleading config. | Remove or wire. |
| T-24 | LOW | runner.py:243-250,291-300; translate.py:144-151; orchestrator.py:472-473 | A4/F7/F8 caches keyed by id:variant only; source not checked; missing entries silently fall back. | Stale/partial caches contaminate runs. | Check source and coverage before a run. |
| T-25 | LOW | orchestrator.py:175-181,214-216 | PG→SQLite few-shot conversion yields invalid SQLite; glossary SQL stays PG under the SQLite label. | Mixed dialect cues (if best_a is A1/A7). | Validate or keep PG with a note. |
| T-26 | LOW | dataset.py:52-57; metrics.py:66-69 | Ambiguous item correct if it matches any one interpretation; guide says clarification or all interpretations. | More lenient than the guide. | Document or tighten. |
| T-27 | LOW | text/normalize.py:75 | Weak Latin key folds ү/у into "o" with ө/о; guide says ө/о→o, ү/у→u. | More false value matches. | Follow guide or document. |
| T-28 | LOW | eval/compare.py:29-33,232-263 | Month/quarter-number transforms applied to both sides lose the year (2025-03 vs 2026-03 matches); 2-decimal rounding + abs_tol 0.01 accepts 0.0532 vs 0.0649. | Rare false positives. | Apply number transforms one-sided; relative tolerance below 1. |
| T-29 | LOW | metrics.py:176; requirements.txt | scipy imported but not declared. | `t2s report` fails on CPU_ONLY install. | Add scipy. |
| T-30 | LOW | service/api.py:155-167 | Audit stores SQL in plaintext; no retrieval context / validator decision; `tables` from candidates[0]. | Security/UX partly met. | Encrypt SQL, log context and guard result, use chosen candidate. |
| T-31 | LOW | orchestrator.py:337 | Empty result re-asked up to repair_rounds (2) times; docs say once. | Docs/behaviour differ. | Cap at 1 or fix docs. |
| T-32 | LOW | db_t2s/100_t2s_views.sql:6,213 | `t2s.collaterals` exposes free-text `description`. | Possible PII / indirect injection. | Remove or document. |
| T-33 | LOW | scripts/server/setup.sh:51,57 | `db-validate … && log` under set -e ignores failure; knowledge-check failure silent. | Broken DB unnoticed. | Check exit codes. |
| T-34 | LOW | security/injection.py:10 | Docstring mentions a non-existent `--live` mode; attacks only against a mock LLM. | Guide asks for real-model runs each release. | Add live mode or fix docstring. |
| T-35 | LOW | retrieval/build.py:86-88 | Missing examples.jsonl only warns → empty example index. | B4/B5 silently without few-shot. | Fail. |
| T-36 | LOW | training/cli.py:104-106; TRAINING.md:18 | human.jsonl not deduped. | Future leakage risk. | Dedup it too. |
| T-37 | LOW | training/grpo.py:127-128; experiments.yaml:73-80 | sqrl GRPO trained single-turn but evaluated with 5 steps. | Train/eval prompt mismatch. | Evaluate sqrl GRPO with max_steps 0 too. |
| T-38 | LOW | bird/runner.py:247-268 | F1 runs write no meta / version fields. | Per-run versioning not met for F1. | Reuse the runner's meta. |
| T-39 | LOW | run_experiments.sh:30; report.py:183 | SPLIT=test overwrites REPORT.md; A3 note prints A3 − A1 (plan: A1 − A3). | Confusing outputs. | REPORT_<split>.md; flip sign. |
| T-40 | LOW | llm/prompts.py:187-190,310-321; EXPERIMENT_PLAN.md:19 | Plan says Arctic's system is "data science expert"; code uses a think/answer system prompt and puts "data science expert" in the user message; not verifiable offline. | Prompt fidelity matters. | Confirm against the model card and align. |

## Spec coverage checklist (tester's view)

Yes: 300/200 split with families on one side; greedy once and 3 seeds; per-question JSONL fields; McNemar; error
analysis (semi-manual); Arctic and sqrl protocols (with T-40 caveat); A1–A7; B2, B5–B10; C1–C5; D1; E1, E2; F1; F2;
F5–F7; knowledge layer; RAG indexes; value index; validation/EXPLAIN/read-only/repairs; voting/abstention; eval set
composition (500; 75% / 12.4% / 12.6%; difficulty 25/54/21); test-suite EX (8 DBs); LLM judge validation.
Partial: one change per run (T-09); per-run versioning (T-18, T-38); clean training data (T-19, T-36); acceptance rule;
metrics (T-11, T-12); environment (T-01); replacement decision (T-20); B1/B3, B4, C6/C7, E3, F3, F8, F9, F10;
security checklist (T-14, T-30, T-32, T-34); UX/feedback; monitoring (no). No: D2–D4 as implemented (T-05, T-06); F4
(T-07).

## Test / lint output

pytest: 35 passed (after the split files existed; earlier 1 skipped). `ruff check src/mn_text2sql` (full rule set,
no --fix): 39 findings (B905 zip strict ×15, I001 ×9, B007 ×7, B023 ×4 — false positives, UP035 ×2, B033, UP032).
`bash -n scripts/server/*.sh`: OK.
