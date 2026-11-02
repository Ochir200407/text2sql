// Шинжээчийн нэмэлт (Studio /builder ба /): SQL-ийн монгол тайлбар, итгэлийн шалтгаан, хязгаарын мэдэгдэл,
// график (inline SVG, гадны сангүй), CSV/Excel татах, 👍/👎, асуултын түүх, үр дүнгийн хураангуй, дараагийн асуулт,
// баталгаажсан асуулга (✅/⭐), SQL засаж дахин ажиллуулах. Сервер: service/analyst.py.
(function () {
  "use strict";
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const css = `
.t2a{display:flex;flex-direction:column;gap:6px;margin-top:6px;font-size:12.5px}
.t2a .conf{display:flex;gap:6px;align-items:baseline;flex-wrap:wrap}
.t2a .lvl{font-size:11px;font-weight:700;padding:1px 8px;border-radius:10px;white-space:nowrap}
.t2a .lvl.high{background:#dcfce7;color:#15803d}.t2a .lvl.medium{background:#fef3c7;color:#b45309}
.t2a .lvl.low,.t2a .lvl.none{background:#fee2e2;color:#b91c1c}.t2a .lvl.unknown{background:#e5e7eb;color:#374151}
.t2a .why{color:var(--muted,#5d6862)}
.t2a ul{margin:2px 0 0 18px;padding:0}
.t2a .ntc{background:#fef3c7;color:#7a5a12;border-radius:6px;padding:4px 8px}
.t2a .ntc.timeout,.t2a .ntc.cost{background:#fee2e2;color:#9f1d1d}
.t2a dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 10px;margin:4px 0 0}
.t2a dt{color:var(--muted,#5d6862)}.t2a dd{margin:0;min-width:0;overflow-wrap:anywhere}
.t2a .acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.t2a .acts a,.t2a .acts button{font:inherit;font-size:11.5px;padding:2px 9px;border-radius:12px;border:1px solid var(--line,#d9ddd8);
  background:var(--surface,#fff);color:var(--fg,#1d2420);text-decoration:none;cursor:pointer;font-weight:400}
.t2a .acts button.on{border-color:var(--accent,#1f6f5c);color:var(--accent,#1f6f5c);font-weight:600}
.t2a .acts .sm{color:var(--muted,#5d6862);font-size:11px}
.t2a svg{display:block;width:100%;height:auto;max-width:560px;overflow:visible}
.t2a svg text{fill:var(--muted,#5d6862);font-size:10px;font-family:inherit}
.t2a svg .ax{stroke:var(--line,#d9ddd8);stroke-width:1}
.t2a svg .mk{fill:var(--accent,#1f6f5c)}.t2a svg .ln{stroke:var(--accent,#1f6f5c);stroke-width:2;fill:none}
.t2a svg .mk:hover{opacity:.75}
.t2a .chart-cap{color:var(--muted,#5d6862);font-size:11px}
.t2h{display:flex;flex-direction:column;gap:2px;max-height:260px;overflow:auto}
.t2h button{font:inherit;font-size:12px;font-weight:400;text-align:left;padding:4px 8px;border:0;border-radius:6px;background:transparent;
  color:var(--fg,#1d2420);cursor:pointer;display:flex;gap:6px;align-items:baseline}
.t2h button:hover{background:var(--code-bg,#eef1ee)}
.t2h .ts{color:var(--muted,#5d6862);font-size:10.5px;margin-left:auto;white-space:nowrap}
.t2h .empty{color:var(--muted,#5d6862);font-size:12px;padding:4px 8px}
.t2a .sum{background:var(--code-bg,#eef1ee);border-radius:6px;padding:5px 9px;line-height:1.45}
.t2a .vq{border:1px solid #bbf7d0;background:#f0fdf4;color:#166534;border-radius:6px;padding:4px 8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.t2a .vq.sim{border-color:#fde68a;background:#fffbeb;color:#7a5a12}
.t2a .vq button,.t2a .fups button{font:inherit;font-size:11.5px;padding:2px 9px;border-radius:12px;border:1px solid var(--line,#d9ddd8);
  background:var(--surface,#fff);color:var(--fg,#1d2420);cursor:pointer;font-weight:400}
.t2a .fups{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.t2a .fups button{border-color:var(--accent,#1f6f5c);color:var(--accent,#1f6f5c)}
.t2a .t2e textarea{width:100%;box-sizing:border-box;font:12px/1.4 ui-monospace,Consolas,monospace;padding:6px;border:1px solid var(--line,#d9ddd8);
  border-radius:6px;background:var(--surface,#fff);color:var(--fg,#1d2420);resize:vertical;margin:4px 0}
.t2a .eout{border-left:3px solid var(--accent,#1f6f5c);padding-left:8px;margin-top:4px}
.t2a .eout table{border-collapse:collapse;font-size:12px;margin:4px 0}
.t2a .eout td,.t2a .eout th{border:1px solid var(--line,#d9ddd8);padding:2px 6px;text-align:left}
.t2a .eout .err{color:#b91c1c}
.t2a .sm{color:var(--muted,#5d6862);font-size:11px}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  const fmtNum = v => Math.abs(v) >= 1000 ? Number(v).toLocaleString("en-US", {maximumFractionDigits: 1}).replace(/,/g, " ")
    : Number(v).toLocaleString("en-US", {maximumFractionDigits: 2});
  const short = (s, n) => { s = String(s ?? "—"); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  // Нэг хэмжигдэхүүн, нэг тэнхлэг; bar — 0-ээс, line — хугацааны дарааллаар. Мөр бүрт <title> (hover tooltip).
  function chartSvg(c) {
    if (!c || !c.points || !c.points.length) return "";
    const pts = c.points, W = 520, H = 200, L = 64, R = 10, T = 10, B = c.type === "bar" ? 46 : 26;
    const ys = pts.map(p => p[1]), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys) || 1;
    const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo || 1));
    const ticks = [lo, lo + (hi - lo) / 2, hi];
    let g = ticks.map(t => `<line class="ax" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" stroke-dasharray="2 3"/>` +
      `<text x="${L - 6}" y="${y(t) + 3}" text-anchor="end">${esc(fmtNum(t))}</text>`).join("");
    const iw = (W - L - R) / pts.length;
    if (c.type === "bar") {
      const bw = Math.max(2, Math.min(36, iw - 2)), every = Math.ceil(pts.length / 12);
      g += pts.map((p, i) => {
        const x = L + i * iw + (iw - bw) / 2, y0 = y(0), y1 = y(p[1]), top = Math.min(y0, y1), h = Math.max(1, Math.abs(y1 - y0));
        const lab = i % every === 0 ? `<text x="${x + bw / 2}" y="${H - B + 12}" text-anchor="end" transform="rotate(-35 ${x + bw / 2} ${H - B + 12})">${esc(short(p[0], 14))}</text>` : "";
        return `<rect class="mk" x="${x}" y="${top}" width="${bw}" height="${h}" rx="${Math.min(4, bw / 2)}"><title>${esc(p[0])}: ${esc(fmtNum(p[1]))}</title></rect>${lab}`;
      }).join("");
    } else {
      const xs = i => L + (pts.length === 1 ? (W - L - R) / 2 : i * (W - L - R) / (pts.length - 1));
      g += `<polyline class="ln" points="${pts.map((p, i) => `${xs(i)},${y(p[1])}`).join(" ")}"/>`;
      g += pts.map((p, i) => `<circle class="mk" cx="${xs(i)}" cy="${y(p[1])}" r="${pts.length > 40 ? 2.5 : 4}"><title>${esc(p[0])}: ${esc(fmtNum(p[1]))}</title></circle>`).join("");
      const idx = [0, Math.floor((pts.length - 1) / 2), pts.length - 1].filter((v, i, a) => a.indexOf(v) === i);
      g += idx.map(i => `<text x="${xs(i)}" y="${H - B + 14}" text-anchor="${i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"}">${esc(short(pts[i][0], 16))}</text>`).join("");
    }
    g += `<line class="ax" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(c.y)} — ${esc(c.x)}-аар">${g}</svg>`;
  }

  function render(a) {
    if (!a || a.error) return "";
    let h = "";
    const c = a.confidence;
    if (c && c.label) {
      h += `<div class="conf"><span class="lvl ${esc(c.level)}">Итгэл: ${esc(c.label)}</span><span class="why">${esc(c.reason)}</span></div>`;
      if (c.tips && c.tips.length) h += `<ul class="why">${c.tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul>`;
    }
    if (a.summary && a.summary.text) h += `<div class="sum" title="Үр дүнгээс шууд тооцоолсон (AI бичээгүй)">📝 ${esc(a.summary.text)}</div>`;
    const v = (a.verified || [])[0];
    if (v) {
      h += v.same_sql ? `<div class="vq">✅ Админ баталгаажуулсан асуулгатай ижил SQL — «${esc(short(v.question, 90))}»</div>`
        : `<div class="vq sim">⭐ Төстэй баталгаажсан асуулга: «${esc(short(v.question, 90))}» (${Math.round(v.score * 100)}%)` +
          (a.sql_edit ? ` <button type="button" data-vq="${esc(v.id)}">▶ Баталгаажсан SQL-ийг ажиллуулах</button>` : "") + `</div>`;
    }
    (a.notices || []).forEach(n => { h += `<div class="ntc ${esc(n.kind)}">${esc(n.text)}</div>`; });
    if (a.chart && a.chart.type && a.chart.type !== "none") {
      h += `<details class="t2chart" open><summary>📊 График: ${esc(a.chart.y)} — ${esc(a.chart.x)}-аар</summary>${chartSvg(a.chart)}` +
        (a.chart.note ? `<div class="chart-cap">${esc(a.chart.note)}</div>` : "") + `</details>`;
    }
    const x = a.sql_explain;
    if (x && x.parts && x.parts.length) {
      h += `<details class="t2x"><summary>🧾 SQL юу хийдэг вэ</summary><dl>${x.parts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></details>`;
    }
    let acts = "";
    if (a.export) {
      acts += `<a data-x="csv" href="#">⤓ CSV</a>`;
      if (a.xlsx) acts += `<a data-x="xlsx" href="#">⤓ Excel</a>`;
    }
    if (a.feedback) acts += `<button type="button" data-v="1" title="Зөв — жишээний нэр дэвшигч болгох">👍</button><button type="button" data-v="0" title="Буруу">👎</button><span class="sm" data-fbst></span>`;
    if (a.sql && new URLSearchParams(location.search).get("admin")) acts += `<button type="button" data-vadd title="Админ: баталгаажсан санд нэмэх">⭐ Баталгаажуулах</button>`;
    if (acts) h += `<div class="acts">${acts}</div>`;
    if (a.followups && a.followups.length) {
      h += `<div class="fups"><span class="sm">Дараагийн асуулт:</span>${a.followups.map((f, i) =>
        `<button type="button" data-fq="${i}" title="${esc(f.question)}">${esc(f.text)}</button>`).join("")}</div>`;
    }
    if (a.sql_edit && a.sql) {
      h += `<details class="t2e"><summary>✏️ SQL засаж дахин ажиллуулах</summary><textarea rows="6" spellcheck="false">${esc(a.sql)}</textarea>` +
        `<div class="acts"><button type="button" data-run>▶ Ажиллуулах</button><span class="sm">Зөвхөн нэг SELECT; SQL guard, мөрийн хязгаар, хугацааны хязгаар хэвээр.</span></div></details>`;
    }
    if (a.sql_edit) h += `<div class="eout" data-eout hidden></div>`;
    return h ? `<div class="t2a">${h}</div>` : "";
  }

  function resultHtml(j) {
    if (j.error) return `<div class="err">${esc(j.error)}</div>` + render(j.analyst);
    const rows = (j.rows || []).slice(0, 50);
    const tbl = j.columns && j.columns.length ? `<div style="overflow:auto"><table><thead><tr>${j.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>` +
      `<tbody>${rows.map(r => `<tr>${r.map(x => `<td>${esc(x ?? "NULL")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
    const more = j.row_count > rows.length ? `<div class="sm">нийт ${j.row_count} мөр, эхний ${rows.length}</div>` : "";
    return `<div class="sm">${j.status === "verified" ? "✅ Баталгаажсан SQL" : "✏️ Засварласан SQL"}-ийн үр дүн</div>${tbl}${more}` + render(j.analyst);
  }

  // root: render()-ийн HTML-ийг агуулсан элемент; opts.key — ?k= түлхүүр
  function bind(root, a, opts) {
    if (!root || !a || !a.rid) return;
    const key = (opts && opts.key) || new URLSearchParams(location.search).get("k");
    const q = p => { const u = new URLSearchParams(p); if (key) u.set("k", key); return "?" + u.toString(); };   // зөвхөн татах холбоосонд
    const admin = new URLSearchParams(location.search).get("admin");
    const hdr = Object.assign({"Content-Type": "application/json"}, key ? {"X-T2S-Key": key} : {}, admin ? {"X-T2S-Admin": admin} : {});
    root.querySelectorAll(".t2a [data-x]").forEach(el => { el.href = `/analyst/export/${encodeURIComponent(a.rid)}` + q({fmt: el.dataset.x}); el.setAttribute("download", ""); });
    const stEl = root.querySelector(".t2a [data-fbst]");
    root.querySelectorAll(".t2a [data-v]").forEach(b => b.onclick = async () => {
      const good = b.dataset.v === "1";
      try {
        const r = await fetch("/analyst/feedback", {method: "POST", headers: hdr,
          body: JSON.stringify({rid: a.rid, good})});
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || r.status);
        root.querySelectorAll(".t2a [data-v]").forEach(x => x.classList.toggle("on", x === b));
        if (stEl) stEl.textContent = good ? (j.candidate_added ? "Баярлалаа — хянуулахаар жишээний нэр дэвшигчид нэмэв"
          : j.duplicate ? "Баярлалаа — энэ хос аль хэдийн нэр дэвшигч" : "Баярлалаа") : "Баярлалаа — тэмдэглэв";
      } catch (e) { if (stEl) stEl.textContent = "Үнэлгээ хадгалагдсангүй: " + e.message; }
    });
    const post = async (url, body) => {
      const r = await fetch(url, {method: "POST", headers: hdr, body: JSON.stringify(body || {})});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : (j.detail ? JSON.stringify(j.detail) : r.status));
      return j;
    };
    // Дараагийн асуулт: бүтэн асуултыг (анхны асуулт + нэмэлт) дахин илгээнэ
    const ask = (opts && opts.ask) || null;
    root.querySelectorAll(".t2a [data-fq]").forEach(b => b.onclick = () => {
      const f = a.followups[+b.dataset.fq]; if (!f) return;
      if (ask) ask(f.question); else navigator.clipboard?.writeText(f.question);
    });
    // SQL засвар ба баталгаажсан SQL: үр дүнг доор нь (дотор нь дахин render + bind) харуулна
    const out = root.querySelector(".t2a [data-eout]");
    const show = async (btn, fn) => {
      if (!out) return;
      btn.disabled = true; out.hidden = false; out.innerHTML = `<div class="sm">Ажиллаж байна…</div>`;
      try { const j = await fn(); out.innerHTML = resultHtml(j); bind(out, j.analyst, opts); }
      catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
      finally { btn.disabled = false; }
    };
    const runBtn = root.querySelector(".t2a .t2e [data-run]");
    if (runBtn) runBtn.onclick = () => show(runBtn, () => post("/analyst/rerun",
      {sql: root.querySelector(".t2a .t2e textarea").value, rid: a.rid}));
    root.querySelectorAll(".t2a [data-vq]").forEach(b => b.onclick = () =>
      show(b, () => post(`/analyst/verified/${encodeURIComponent(b.dataset.vq)}/run`)));
    const vadd = root.querySelector(".t2a [data-vadd]");
    if (vadd) vadd.onclick = async () => {
      try {
        const j = await post("/analyst/verified", {rid: a.rid});
        vadd.textContent = j.duplicate ? `⭐ Аль хэдийн баталгаажсан (${j.id})` : `⭐ Баталгаажсан санд нэмэв (${j.id})`;
        vadd.disabled = true;
      } catch (e) { vadd.textContent = "⭐ Алдаа: " + e.message; }
    };
  }

  // Асуултын түүх: зөвхөн энэ хөтөчид (localStorage) — сервер рүү илгээхгүй
  const HK = "t2s_history";
  const history = {
    list() { try { return JSON.parse(localStorage.getItem(HK) || "[]"); } catch { return []; } },
    add(q, status) {
      q = String(q || "").trim(); if (!q) return;
      const l = this.list().filter(x => x.q !== q); l.unshift({q, status: status || null, t: Date.now()});
      try { localStorage.setItem(HK, JSON.stringify(l.slice(0, 30))); } catch { /* private mode */ }
    },
    clear() { try { localStorage.removeItem(HK); } catch { /* private mode */ } },
  };
  const ST = {answered: "✓", refused: "⛔", clarify: "❓", abstained: "🤔", error: "✗"};
  function renderHistory(el, onPick) {
    const l = history.list();
    el.innerHTML = l.length ? `<div class="t2h">${l.map((x, i) => `<button type="button" data-i="${i}" title="Дахин ажиллуулах">` +
      `<span>${ST[x.status] || "•"}</span><span>${esc(x.q)}</span><span class="ts">${esc(new Date(x.t).toLocaleString("mn-MN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}))}</span></button>`).join("")}</div>`
      : `<div class="t2h"><div class="empty">Түүх хоосон.</div></div>`;
    el.querySelectorAll(".t2h [data-i]").forEach(b => b.onclick = () => onPick(l[+b.dataset.i].q));
  }

  window.T2SAnalyst = {render, bind, chartSvg, history, renderHistory};
})();
