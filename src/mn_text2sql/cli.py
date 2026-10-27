"""Командын мөрийн интерфейс:  python -m mn_text2sql <команд> [--config path] ...

Өгөгдөл ба мэдлэг
  db-build         DB үүсгэж схем + синтетик өгөгдөл + t2s давхарга ачаална
  db-t2s           зөвхөн db_t2s/*.sql-г дахин ачаална (view, эрх, RLS)
  db-validate      db/validation + FK өнчин мөрийн шалгалт
  testsuite-build  test-suite accuracy-д зориулсан хувилбар DB-үүд (nbfi_ts1..N)
  index-build      мэдлэгийн индекс (схем, утга, жишээ, нэр томьёо)
  knowledge-check  semantic model, glossary, metrics.yaml-г DB-тэй тулгана
  evalset-build    evalset/drafts → dev.jsonl (300) + test.jsonl (200) + examples + MANIFEST
  check-gold       eval set / жишээний сангийн gold SQL-ийг шалгана (--testsuite: хуулбар DB дээр)
Туршилт
  ask              нэг асуулт асууж SQL + үр дүн харуулна
  eval             туршилтын ID (A1…F10) ажиллуулна → results/runs/<split>/<ID>/*.jsonl
  ts-score         run-ийн EX-ээр зөв бичлэгийг test-suite DB-үүд дээр дахин шалгана (TS-EX)
  derive-abstain   E3: E2-ийн бичлэгээс босгоор татгалзалт гаргана
  reselect         E4: өмнөх run-ий хувилбарыг дахин гүйцэтгэж шинэ дүрмээр дахин сонгоно (загваргүй)
  score-external   гадаад системийн таамгийг (одоогийн систем г.м.) ижил аргаар үнэлнэ
  report           results/REPORT.md (төлөвлөгөөний хүснэгтийн формат, McNemar)
  report-latex     төгсөлтийн ажлын тайлангийн (report/) хүснэгтүүд → report/generated/*.tex
  errors           алдааны шинжилгээний түүвэр (NL2SQL-BUGs + монгол шошго) → CSV
  judge            LLM judge (туслах; judge-validate-ээр ≥200 хүний шошготой тулгасны дараа)
  translate        A4: орчуулгын кэш (орчуулагч LLM)
  intent           F7: intent JSON кэш (жижиг монгол загвар)
  route            F8: semantic router-ийн JSON кэш
  select-annotations F9: санал зөрөлтөөр ба санамсаргүйгээр 100 асуулт сонгох
Аюулгүй байдал ба үйлчилгээ
  security-test    prompt-injection (монгол + латин) ба SQL guard/DB хамгаалалтын тест
  serve            FastAPI үйлчилгээ (shadow/pilot горим)
Thesis benchmark (--config config/thesis.yaml; docs/THESIS_BENCH.md)
  thesis-import    text2sql-thesis-ийн DB + 150 кейсийг (зөвхөн уншиж) шинэ систем рүү: PostgreSQL thesis_ecom, data/thesis
  thesis-score     thesis-ийн өөрийн eval-ийн JSON-ийг ижил шалгуураар оноолох → EXT-<name> run
  thesis-compare   thesis-ийн систем vs шинэ системийн run бүр (EX, McNemar) → results/thesis/COMPARE.md
Сургалт: python -m mn_text2sql.training --help
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "urllib3", "sentence_transformers", "filelock", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def _csv(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _items_for(cfg, split: str):
    from .eval.dataset import load_items
    from .eval.runner import split_files
    return load_items(split_files(cfg, split))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mn_text2sql", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="нэмэлт YAML тохиргоо (config/default.yaml-ийг дарна)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("db-build")
    p.add_argument("--db", help="DB нэр (анхдагч: db.name)")
    p.add_argument("--seed", type=int)
    p.add_argument("--scale", type=float)
    p.add_argument("--keep", action="store_true", help="байгаа DB-г устгахгүй (алдаа гарна)")
    sub.add_parser("db-t2s").add_argument("--db")
    sub.add_parser("db-validate").add_argument("--db")

    p = sub.add_parser("testsuite-build")
    p.add_argument("--count", type=int)
    p.add_argument("--scale", type=float)

    p = sub.add_parser("index-build")
    p.add_argument("--no-embed", action="store_true", help="embedding-гүй (зөвхөн BM25) индекс")
    p.add_argument("--extra-examples", help="F9: жишээний санд нэмэх JSONL (тэмдэглэсэн асуултууд)")
    p.add_argument("--out", help="индексийн хавтас (анхдагч: knowledge.index_dir), F9: build/index_f9a")
    p.add_argument("--dev-examples", action="store_true",
                   help="I бүлэг: dev-ийн хариулах боломжтой асуултыг жишээний санд нэмнэ (test хэзээ ч үгүй)")
    p.add_argument("--glossary", help="J: өөр нэр томьёоны файл (knowledge/glossary_plus.yaml); анхдагч knowledge.glossary")

    sub.add_parser("knowledge-check")

    p = sub.add_parser("evalset-build")
    p.add_argument("--dev", type=int, default=300)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--version", default="v1")
    p.add_argument("--overwrite", action="store_true")

    p = sub.add_parser("check-gold")
    p.add_argument("files", nargs="*")
    p.add_argument("--testsuite", action="store_true")

    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--run", default="B5", help="туршилтын ID (тохиргоо), анхдагч B5")
    p.add_argument("--model", help="config/models.yaml-ийн нэр (анхдагч: llm.model)")
    p.add_argument("--variant", default="mn")
    p.add_argument("--mock-sql", help="LLM-ийн оронд энэ SQL-ийг буцаах (туршилт)")

    p = sub.add_parser("eval")
    p.add_argument("--run", required=True, help="туршилтын ID-ууд таслалаар: A1 эсвэл A1,A6,A7")
    p.add_argument("--model", help="зөвхөн энэ загвар (анхдагч: тохиргооны models)")
    p.add_argument("--split", default="dev", help="dev | test | all | JSONL зам")
    p.add_argument("--variants", help="mn,latin,en,mixed (анхдагч: тохиргооны variant)")
    p.add_argument("--limit", type=int)
    p.add_argument("--ids", help="зөвхөн эдгээр асуулт (таслалаар)")
    p.add_argument("--workers", type=int)
    p.add_argument("--seeds", help="sampling-ийн seed-үүд (таслалаар), анхдагч тохиргооноос")
    p.add_argument("--oracle", action="store_true", help="gold SQL-ийг таамаг болгон (harness шалгах)")
    p.add_argument("--tag", default="", help="нэмэлт шошго (үндсэн тайланд орохгүй)")
    p.add_argument("--set", action="append", help="тохиргоог дарах: key=value (давтаж болно)")
    p.add_argument("--var", action="append", help="experiments.yaml-ийн vars: best_a=A7")
    p.add_argument("--overwrite", action="store_true", help="өмнөх үр дүнг устгаж шинээр")

    p = sub.add_parser("ts-score")
    p.add_argument("files", nargs="*", help="results/runs/.../*.jsonl")
    p.add_argument("--run", help="эсвэл туршилтын ID-гийн бүх файл")
    p.add_argument("--split", default="dev")

    p = sub.add_parser("derive-abstain")
    p.add_argument("--from", dest="src", default="E2")
    p.add_argument("--to", dest="dst", default="E3")
    p.add_argument("--threshold", type=float, required=True)
    p.add_argument("--split", default="dev")
    p.add_argument("--action", choices=["abstain", "clarify"], default="abstain",
                   help="санал нийлэлт бага үед: татгалзах (E3) эсвэл тодруулга асуух (E3c)")
    p.add_argument("--tag", default="", help="эх run-ийн tag (жишээ нь h_H4)")

    p = sub.add_parser("reselect")
    p.add_argument("--from", dest="src", default="E2")
    p.add_argument("--to", dest="dst", default="E4")
    p.add_argument("--split", default="dev")
    p.add_argument("--tag", default="", help="эх run-ийн tag (жишээ нь h_H4)")
    p.add_argument("--no-filter", action="store_true", help="хоосон/0/DB-д байхгүй утгатай хувилбарыг хасахгүй")
    p.add_argument("--no-refuse", action="store_true", help="дүрмийн татгалзал (AUTHORING ангилал, хүснэгтгүй SQL) үгүй")
    p.add_argument("--no-clarify", action="store_true", help="дүрмийн тодруулга үгүй")
    p.add_argument("--clarify-threshold", type=float, help="санал нийлэлт < босго бол тодруулга (E3c шиг)")
    p.add_argument("--extra", nargs="*", default=None,
                   help="E5: хамт санал хураах нэмэлт run-ууд (жишээ нь I3t; <model>__<variant>__s0.jsonl)")
    p.add_argument("--extra-weight", type=int, default=1, help="нэмэлт run-ийн хувилбар бүрийн жин (давталт)")

    p = sub.add_parser("score-external")
    p.add_argument("--pred", required=True, help="JSONL: {question_id, sql, variant?, status?, latency_ms?}")
    p.add_argument("--name", required=True)
    p.add_argument("--split", default="dev")
    p.add_argument("--variant", default="mn")

    p = sub.add_parser("compare", help="хоёр run-ийг McNemar-аар (жишээ: одоогийн систем EXT-* ба шинэ систем)")
    p.add_argument("--a", required=True, help="суурь run (.jsonl)")
    p.add_argument("--b", required=True, help="шинэ run (.jsonl)")

    p = sub.add_parser("report")
    p.add_argument("--split", default="dev")
    p.add_argument("--out", default="results/REPORT.md")

    p = sub.add_parser("report-latex", help="report/ (ШУТИС LaTeX) хүснэгтүүдийг results/-аас үүсгэх")
    p.add_argument("--out", default="report/generated")

    p = sub.add_parser("errors")
    p.add_argument("--file", help="run-ийн JSONL")
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--summary-csv", help="хүн шошголсон CSV-ийн эцсийн тархалт")

    for name in ("translate", "intent", "route"):
        p = sub.add_parser(name)
        p.add_argument("--split", default="dev")
        p.add_argument("--variants", default="mn,latin,mixed")
        p.add_argument("--model", help="config/models.yaml-ийн нэр")
        p.add_argument("--base-url", help="тухайн загварын vLLM хаяг (анхдагч: llm.base_url)")
        p.add_argument("--workers", type=int, default=8)
        if name == "translate":
            p.add_argument("--t2", action="store_true",
                           help="T2: маскыг DB-ийн нэрээр шүүх + нэр томьёоны RAG → <орчуулагч>.t2.jsonl")
            p.add_argument("--format", choices=["v1", "sft"],
                           help="sft = fine-tune хийсэн орчуулагчийн prompt (M1) → <орчуулагч>.sft.jsonl "
                                "(анхдагч: models.yaml translator_prompt)")

    p = sub.add_parser("judge", help="LLM judge (туслах хэмжүүр) — run-ийн буруу бичлэгүүдийг дахин үнэлнэ")
    p.add_argument("--file", required=True)
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--all", action="store_true", help="зөвхөн буруу биш, бүх бичлэг")
    p = sub.add_parser("judge-validate", help="judge ба хүний шошгын тохирол (≥200, Cohen's κ)")
    p.add_argument("--judge-file", required=True)
    p.add_argument("--human-csv", required=True)

    sub.add_parser("translit-learn", help="F3: audit + feedback лог-оос галиг→кирилл хос (build/translit_pairs.jsonl)")

    p = sub.add_parser("select-annotations")
    p.add_argument("--pool-run", required=True, help="шошгогүй асуултын сан дээрх E2 маягийн run (8 хувилбар)")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)

    p = sub.add_parser("bird-prepare", help="F1: BIRD dev татаж дэд олонлог сонгох")
    p.add_argument("--subset", type=int)
    p.add_argument("--seed", type=int)
    p.add_argument("--url")
    p = sub.add_parser("bird-translate", help="F1: англи → монгол орчуулга (орчуулагч LLM)")
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--workers", type=int, default=8)
    sub.add_parser("bird-review-export", help="F1: хүний шалгалтын CSV")
    p = sub.add_parser("bird-review-import", help="F1: засварласан CSV-г буцааж оруулах")
    p.add_argument("--csv")

    p = sub.add_parser("security-test")
    p.add_argument("--live", action="store_true", help="injection асуултыг ЖИНХЭНЭ загвараар (vLLM асаалттай) ажиллуулах")
    p.add_argument("--model", help="--live үед загвар (анхдагч: llm.model)")

    p = sub.add_parser("thesis-import", help="text2sql-thesis-ийн DB + кейс → data/thesis, PostgreSQL (--config config/thesis.yaml)")
    p.add_argument("--src", default="../text2sql-thesis")
    p.add_argument("--overwrite", action="store_true", help="PostgreSQL DB-г устгаад дахин үүсгэх")
    p = sub.add_parser("thesis-score", help="thesis-ийн results/*.json-ийг ижил шалгуураар оноолох")
    p.add_argument("--file", action="append", required=True, help="thesis-ийн eval JSON (давтаж болно: dev, test)")
    p.add_argument("--name", default="thesis-agent")
    p.add_argument("--dialect", default="sqlite")
    p.add_argument("--llm", default=None, help="thesis-ийн системийн ашигласан загвар (тайлангийн хүснэгтэд)")
    p = sub.add_parser("thesis-compare", help="thesis-ийн систем vs шинэ систем → results/thesis/COMPARE.md")
    p.add_argument("--reference", default="EXT-thesis-agent")
    p.add_argument("--out", default="results/thesis/COMPARE.md")

    p = sub.add_parser("serve")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--run", default=None, help="production тохиргоо (анхдагч: config pipeline)")
    p = sub.add_parser("flow-ui", help="Flow builder (загвар, DB-гүй угсрах горим): http://localhost:8090/builder")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--demo", action="store_true", help="Локал DB + AI-гүй демо ажиллуулалт (SQL = хамгийн төстэй жишээ)")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    from .config import load_config
    cfg = load_config(args.config)

    if args.cmd == "db-build":
        from .datagen.runner import generate_database
        result = generate_database(cfg, dbname=args.db, seed=args.seed, scale=args.scale, drop=not args.keep)
        _print({k: v for k, v in result.items() if k != "rows"} | {"total_rows": sum(result["rows"].values())})
        return 0 if not result["fk_orphans"] and not result["validation_failed"] else 2
    if args.cmd == "db-t2s":
        from .db.loader import load_t2s_layer
        load_t2s_layer(cfg, args.db or cfg["db"]["name"])
        return 0
    if args.cmd == "db-validate":
        from .datagen.runner import check_fk_orphans
        from .db.conn import admin_dsn, connection
        from .db.loader import run_validation
        name = args.db or cfg["db"]["name"]
        rows = run_validation(cfg, name)
        with connection(admin_dsn(cfg, name)) as conn:
            orphans = check_fk_orphans(conn)
        _print({"validation": rows, "fk_orphans": orphans})
        return 0 if not orphans and all(r[2] == r[3] for r in rows) else 2
    if args.cmd == "testsuite-build":
        from .eval.testsuite import build_testsuite
        res = build_testsuite(cfg, count=args.count, scale=args.scale)
        _print(res)
        return 0 if all(d["fk_orphans"] == 0 for d in res["dbs"]) else 2
    if args.cmd == "index-build":
        from .retrieval.build import build_indexes
        if args.out:
            cfg["knowledge"]["index_dir"] = args.out
        if args.glossary:
            if not args.out:
                raise SystemExit("--glossary нь --out-той хамт (анхдагч build/index-ийг өөр glossary-гоор дарж бичвэл "
                                 "бусад тохиргооны glossary_emb.npy мөрийн тоо зөрнө)")
            cfg["knowledge"]["glossary"] = args.glossary
        _print(build_indexes(cfg, use_embeddings=not args.no_embed, extra_examples=args.extra_examples,
                             include_dev=args.dev_examples))
        return 0
    if args.cmd == "knowledge-check":
        from .knowledge.check import check_knowledge
        res = check_knowledge(cfg)
        _print(res)
        return 0 if not res["errors"] else 2
    if args.cmd == "evalset-build":
        from .eval.split import build_evalset
        _print(build_evalset(cfg, n_dev=args.dev, seed=args.seed, version=args.version, overwrite=args.overwrite))
        return 0
    if args.cmd == "check-gold":
        from .eval.goldcheck import check_gold_files
        result = check_gold_files(cfg, args.files, testsuite=args.testsuite)
        _print(result)
        return 0 if not result.get("errors") else 2
    if args.cmd == "ask":
        from .pipeline.orchestrator import Pipeline
        pipe = Pipeline.from_config(cfg, mock_sql=args.mock_sql, run_id=args.run, model_name=args.model)
        _print(pipe.ask(args.question, args.variant).to_dict())
        return 0
    if args.cmd == "eval":
        from .eval.runner import run_experiment
        from .pipeline.options import parse_set
        out = []
        for rid in _csv(args.run):
            out += run_experiment(cfg, rid, model=args.model, split=args.split, variants=_csv(args.variants),
                                  limit=args.limit, workers=args.workers, oracle=args.oracle, tag=args.tag,
                                  var_overrides=parse_set(args.var), set_overrides=parse_set(args.set),
                                  seeds=[int(s) for s in _csv(args.seeds)] if args.seeds else None,
                                  overwrite=args.overwrite, ids=_csv(args.ids))
        _print(out)
        return 0
    if args.cmd == "ts-score":
        from .config import resolve_path
        from .eval.runner import ts_score_files
        files = list(args.files)
        if args.run:
            root = resolve_path(cfg["eval"]["results_dir"]) / "runs" / args.split / args.run
            from .eval.report import RUN_FILE
            files += [str(p) for p in sorted(root.glob("*.jsonl")) if RUN_FILE.fullmatch(p.name)]
        _print(ts_score_files(cfg, files))
        return 0
    if args.cmd == "derive-abstain":
        from .eval.report import derive_abstention
        _print(derive_abstention(cfg, args.src, args.dst, args.threshold, args.split, args.action, args.tag))
        return 0
    if args.cmd == "reselect":
        from .eval.reselect import reselect_run
        _print(reselect_run(cfg, args.src, args.dst, args.split, args.tag, vote_filter=not args.no_filter,
                            rule_refuse=not args.no_refuse, rule_clarify=not args.no_clarify,
                            clarify_threshold=args.clarify_threshold, extra=args.extra,
                            extra_weight=args.extra_weight))
        return 0
    if args.cmd == "score-external":
        from .eval.runner import score_external
        _print(score_external(cfg, args.pred, args.name, args.split, args.variant))
        return 0
    if args.cmd == "compare":
        from .eval.metrics import compare_runs
        _print(compare_runs(args.a, args.b, cfg["eval"]["penalty_c"]))
        return 0
    if args.cmd == "report":
        from .eval.report import build_report
        _print(build_report(cfg, out=args.out, split=args.split))
        return 0
    if args.cmd == "report-latex":
        from .eval.latex import build_latex
        _print(build_latex(cfg, args.out))
        return 0
    if args.cmd == "errors":
        from .eval.errors import error_sample, manual_summary
        if args.summary_csv:
            _print(manual_summary(args.summary_csv))
        elif args.file:
            _print(error_sample(cfg, args.file, n=args.n, seed=args.seed))
        else:
            sys.stderr.write("--file эсвэл --summary-csv шаардлагатай\n")
            return 1
        return 0
    if args.cmd in ("translate", "intent", "route"):
        items = _items_for(cfg, args.split)
        variants = _csv(args.variants)
        if args.cmd == "translate":
            from .pipeline.translate import translate_items
            _print(translate_items(cfg, items, variants, args.model, args.base_url, args.workers, t2=args.t2, fmt=args.format))
        elif args.cmd == "intent":
            from .pipeline.intent import build_intents
            _print(build_intents(cfg, items, variants, args.model, args.base_url, args.workers))
        else:
            from .pipeline.router import build_routes
            _print(build_routes(cfg, items, variants, args.model, args.base_url, args.workers))
        return 0
    if args.cmd == "judge":
        from .eval.judge import run_judge
        _print(run_judge(cfg, args.file, args.model, args.base_url, only_wrong=not args.all))
        return 0
    if args.cmd == "judge-validate":
        from .eval.judge import validate_judge
        _print(validate_judge(args.judge_file, args.human_csv))
        return 0
    if args.cmd == "translit-learn":
        from .text.learned import build_pairs_from_logs
        _print(build_pairs_from_logs(cfg))
        return 0
    if args.cmd == "select-annotations":
        from .eval.annotate import select_annotations
        _print(select_annotations(cfg, args.pool_run, n=args.n, seed=args.seed))
        return 0
    if args.cmd.startswith("bird-"):
        from .bird import data as bird
        if args.cmd == "bird-prepare":
            _print(bird.prepare(cfg, args.subset, args.seed, args.url))
        elif args.cmd == "bird-translate":
            _print(bird.translate(cfg, args.model, args.base_url, args.workers))
        elif args.cmd == "bird-review-export":
            _print(bird.review_export(cfg))
        else:
            _print(bird.review_import(cfg, args.csv))
        return 0
    if args.cmd == "security-test":
        from .security.injection import run_security_suite
        result = run_security_suite(cfg, live=args.live, model=args.model)
        _print(result)
        return 0 if result.get("all_blocked") else 2
    if args.cmd in ("thesis-import", "thesis-score", "thesis-compare"):
        if cfg["db"]["name"] == "nbfi":
            print("Thesis benchmark-ийг --config config/thesis.yaml-тай ажиллуулна (ББСБ-ийн DB-д хүрэхгүйн тулд)")
            return 2
        from .thesis import bench
        if args.cmd == "thesis-import":
            _print(bench.import_thesis(cfg, args.src, overwrite=args.overwrite))
        elif args.cmd == "thesis-score":
            _print(bench.score_thesis_results(cfg, args.file, args.name, args.dialect, args.llm))
        else:
            _print(bench.compare_report(cfg, reference=args.reference, out=args.out))
        return 0
    if args.cmd == "flow-ui":
        import uvicorn

        from .service.flow import create_builder_app
        uvicorn.run(create_builder_app(demo=args.demo), host=args.host, port=args.port, log_level="info")
        return 0
    if args.cmd == "serve":
        from .service.api import serve
        serve(cfg, host=args.host, port=args.port, run_id=args.run)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
