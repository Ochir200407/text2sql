# Архитектур

```
асуулт (кирилл / латин / англи / холимог)
  │
  ├─ [A4] орчуулга + placeholder  (pipeline/translate.py, кэш)    ├─ [F7] intent JSON (pipeline/intent.py, кэш)
  ├─ [F8] semantic router → KPI compiler (pipeline/router.py, knowledge/metrics.yaml) ── таарвал шууд SQL
  ▼
мэдлэг ба хайлт (retrieval/)
  схемийн индекс (BM25 + bge-m3) · утгын индекс (кирилл + сул латин түлхүүр, fuzzy; F2 дагавар/галиг, F3 сургасан)
  нэр томьёо (glossary) · DAIL жишээ (утгыг mask хийсэн асуулт + SQL skeleton) · schema linking (B10)
  ▼
prompt (llm/prompts.py) — загвар тус бүрийн ӨӨРИЙН протокол
  omnisql (Arctic): DDL (+ монгол comment, токен төсөв), evidence, «Let me solve…<think>» prefill, ```sql``` <answer>
  sqrl: <schema>, <evidence>, <sql> судлах (≤ 5 алхам, F10 <search_values>) / <answer>
  dialect: SQLite (A1) | PostgreSQL-ийг заах (A6) | SQLite → sqlglot + огнооны дүрэм (A7)
  ▼
vLLM (OpenAI API) — n хувилбар, seed, prefill, context-ийн хамгаалалт (llm/client.py)
  ▼
шалгах ба гүйцэтгэх: sqlglot guard (allow-list, хориотой функц, as-of огноо) → EXPLAIN өртөг → READ ONLY + timeout
  алдаа / хоосон → засах (E1, 1–3 удаа) · олон хувилбар → үр дүнгийн hash-аар санал хураах (E2) → итгэл → татгалзах (E3)
  ▼
хариулт: SQL, үр дүн, ашигласан хүснэгт, монгол тайлбар, итгэл  (service/api.py: shadow/pilot, audit, feedback)
```

## Үнэлгээ

`eval/runner.py`: туршилтын ID → `RunOptions` (`pipeline/options.py`, experiments.yaml-ийн base гинж ба vars) →
`Pipeline` → `score_answer` (gold-ийн кэш, уян харьцуулалт, хувилбар бүрийн зөв эсэх) → JSONL + summary + meta.
`eval/testsuite.py` (8 хуулбар DB), `eval/metrics.py`, `eval/report.py` (McNemar, хүлээн авах дүрэм),
`eval/errors.py` (NL2SQL-BUGs), `eval/judge.py`, `eval/annotate.py` (F9), `bird/` (F1, SQLite).

## Өгөгдөл

`datagen/` нь тогтмол seed-тэй generator: харилцагч, зээлийн хүсэлт, скоринг, зээл (хуваарь, хуваарилалт, DPD, ангилал,
сан), барьцаа, батлан даагч, данс/гүйлгээ/хадгаламж, барагдуулалт, ерөнхий дэвтэр. Бизнес түлхүүрийг он цагийн
дарааллаар дугаарлаж COPY-оор ачаална. FK өнчин мөр 0, DB-ийн validation бүгд давна.

## Сургалт

`training/`: data (BIRD train + BIRDTurk орчуулга + dedup) → synth (C3) → sampling (C1, C2, F10) → formats (chat
template, D2 RAG prompt, F6 pivot, C4 англи хувь) → sft (TRL + PEFT) → grpo (TRL GRPOTrainer + vLLM, гүйцэтгэлийн
reward, F4 бонус) → general_eval (Belebele).
