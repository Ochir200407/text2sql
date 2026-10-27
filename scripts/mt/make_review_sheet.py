"""Хүний шалгалтын хуудас (150 хос, ~30 мин): data/mt/v1/pairs.jsonl-ээс домэйн × register-ээр тэгшитгэн санамсаргүй сонгож,
ганц HTML файл (data/mt/v1/human_review.html) болгоно. Хөтөчөөр нээж хос бүрийг «Зөв / Засах / Буруу»-гаар тэмдэглэнэ (хөтөчид хадгалагдана),
дараа нь «JSON хуулах»-аар үр дүнг буцааж өгнө. docs/MT_INVARIANT_PLAN.md §5.6 — ≥ 90% зөв бол D4-ийг өргөжүүлнэ.

Ажиллуулах:  python scripts/mt/make_review_sheet.py [--n 150] [--pairs data/mt/v1/pairs.jsonl]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PAGE = """<!doctype html>
<html lang="mn"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Орчуулгын хосын шалгалт</title>
<style>
:root{--bg:#fafafa;--fg:#1c1c1e;--card:#fff;--line:#d9d9de;--ok:#1b7f3b;--fix:#b26a00;--bad:#b3261e;--mut:#6b6b74}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--card:#202024;--line:#3a3a40;--ok:#5fd08a;--fix:#f0b45a;--bad:#ff8a80;--mut:#9a9aa6}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;z-index:2}
header b{font-size:18px} .bar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:6px;color:var(--mut);font-size:14px}
main{max-width:860px;margin:0 auto;padding:12px 16px 80px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
.card.ok{border-left:5px solid var(--ok)} .card.fix{border-left:5px solid var(--fix)} .card.bad{border-left:5px solid var(--bad)}
.meta{font-size:12px;color:var(--mut)} .mn{font-size:18px;margin:6px 0} .en{margin:4px 0 8px;color:var(--fg)}
.btns button{font:inherit;padding:6px 14px;margin-right:8px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--fg);cursor:pointer}
.btns button.sel{font-weight:600;border-width:2px} .btns .ok.sel{border-color:var(--ok);color:var(--ok)}
.btns .fix.sel{border-color:var(--fix);color:var(--fix)} .btns .bad.sel{border-color:var(--bad);color:var(--bad)}
textarea{width:100%;box-sizing:border-box;margin-top:8px;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.export{position:fixed;right:16px;bottom:16px;padding:10px 18px;font:inherit;border-radius:24px;border:0;background:#2563eb;color:#fff;cursor:pointer}
</style></head><body>
<header><b>Орчуулгын хосын шалгалт</b>
<div class="bar"><span id="prog"></span><span>Монгол ↔ англи: утга нэмэгдсэн/хасагдсан, нэр томьёо буруу, монгол хэллэг хачин бол «Засах» эсвэл «Буруу».</span></div></header>
<main id="list"></main>
<button class="export" onclick="exportJson()">JSON хуулах</button>
<script>
const ITEMS = __ITEMS__;
const KEY = "mt_review_v1";
let state = {}; try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
function save(){ try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }
function esc(s){ return String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function render(){
  const list = document.getElementById("list"); list.innerHTML = "";
  let done = 0;
  ITEMS.forEach(it => {
    const st = state[it.id] || {}; if (st.v) done++;
    const el = document.createElement("div"); el.className = "card " + (st.v || "");
    el.innerHTML = `<div class="meta">${esc(it.id)} · ${esc(it.domain)} · ${esc(it.register || "")}</div>
      <div class="mn">${esc(it.mn)}</div><div class="en">${esc(it.en)}</div>
      <div class="btns">
        <button class="ok ${st.v==="ok"?"sel":""}" onclick="setV('${it.id}','ok')">Зөв</button>
        <button class="fix ${st.v==="fix"?"sel":""}" onclick="setV('${it.id}','fix')">Засах</button>
        <button class="bad ${st.v==="bad"?"sel":""}" onclick="setV('${it.id}','bad')">Буруу</button></div>
      ${st.v && st.v!=="ok" ? `<textarea rows="2" placeholder="Зөв хувилбар эсвэл шалтгаан" oninput="setN('${it.id}',this.value)">${esc(st.n||"")}</textarea>` : ""}`;
    list.appendChild(el);
  });
  const ok = Object.values(state).filter(s => s.v==="ok").length;
  document.getElementById("prog").textContent = `${done}/${ITEMS.length} шалгасан · зөв ${ok}`;
}
function setV(id, v){ state[id] = Object.assign(state[id]||{}, {v}); save(); render(); }
function setN(id, n){ state[id] = Object.assign(state[id]||{}, {n}); save(); }
function exportJson(){
  const out = ITEMS.map(it => ({id: it.id, verdict: (state[it.id]||{}).v || null, note: (state[it.id]||{}).n || ""}));
  const txt = JSON.stringify(out, null, 1);
  navigator.clipboard.writeText(txt).then(() => alert("Хуулагдлаа — чат руу буулгана уу."), () => { prompt("Хуулж аваад чат руу буулгана уу:", txt); });
}
render();
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--pairs", default="data/mt/v1/pairs.jsonl")
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()
    rows = [json.loads(x) for x in (ROOT / args.pairs).read_text(encoding="utf-8").splitlines() if x.strip()]
    cand = [r for r in rows if r.get("source") in ("v1_draft", "phrase_draft")] or rows   # агентын бичсэн хосыг л шалгана
    rng = random.Random(args.seed)
    by: dict[tuple, list] = defaultdict(list)
    for r in cand:
        by[(r["domain"], r.get("register", ""))].append(r)
    keys = sorted(by)
    per = max(1, args.n // len(keys))
    pick = []
    for k in keys:
        rng.shuffle(by[k])
        pick += by[k][:per]
    rest = [r for r in cand if r not in pick]
    rng.shuffle(rest)
    pick += rest[: max(0, args.n - len(pick))]
    rng.shuffle(pick)
    items = [{"id": r["id"], "domain": r["domain"], "register": r.get("register", ""), "mn": r["mn"], "en": r["en"]}
             for r in pick[: args.n]]
    html = PAGE.replace("__ITEMS__", json.dumps(items, ensure_ascii=False).replace("</", "<\\/"))
    out = (ROOT / args.pairs).parent / "human_review.html"   # pairs-ийн хавтсанд (v1, v2 …)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"{len(items)} хос → {out}")


if __name__ == "__main__":
    main()
