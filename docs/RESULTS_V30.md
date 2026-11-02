# V26-ийн үр дүн (Kaggle гаралт)

Үүсгэсэн: `scripts/ingest/ingest_results.py --tag V26`, 2026-11-02. EX = зөв / нийт; CI бүр Wilson score 95% интервал (z = 1.96); McNemar exact (p = 2·P(X ≤ min(b,c)), X ~ Bin(b+c, ½)). Түвшин ба домэйн нь зөвхөн хариулах боломжтой асуулт дээр.

Тэмдэглэгээ: † зөв id-ийн жагсаалт (*_answerable_correct.txt): зөвхөн хариулах боломжтой асуулт; дутуу асуулт харагдахгүй тул бүрэн эсэх нь шалгагдахгүй — нийт EX ба нийт McNemar-д ороогүй. * дутуу/буруу run, --force-оор оруулсан (n нь бодит мөрийн тоо, McNemar нь давхцсан асуулт дээр).

## Headline (урьдчилан зарласан)

Headline (урьдчилан зарласан, --headline M1ens): M1ens нийт 110/200 (55.0%; Wilson 95% CI 48.1–61.7), хариулах 90/151 (59.6%; Wilson 95% CI 51.6–67.1)

## Оруулсан run-ууд

- dev/A1 [jsonl] results/runs/dev/A1/arctic-r1-7b__en__s0.jsonl n=300 → results/runs/dev/A1: —; алгасав: arctic-r1-7b__en__s0.jsonl (ижил), arctic-r1-7b__en__s0.summary.json (ижил), arctic-r1-7b__en__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/A6 [jsonl] results/runs/dev/A6/arctic-r1-7b__en__s0.jsonl n=300 → results/runs/dev/A6: —; алгасав: arctic-r1-7b__en__s0.jsonl (ижил), arctic-r1-7b__en__s0.summary.json (ижил), arctic-r1-7b__en__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/A7 [jsonl] results/runs/dev/A7/arctic-r1-7b__en__s0.jsonl n=300 → results/runs/dev/A7: —; алгасав: arctic-r1-7b__en__s0.jsonl (ижил), arctic-r1-7b__en__s0.summary.json (ижил), arctic-r1-7b__en__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/C1 [jsonl] results/runs/dev/C1/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/C1: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (байгаа, --force) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/C2 [jsonl] results/runs/dev/C2/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/C2: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (байгаа, --force) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/C3 [jsonl] results/runs/dev/C3/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/C3: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (байгаа, --force) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/E2 [jsonl] results/runs/dev/E2/arctic-r1-7b__mn__s1__h_H4.jsonl n=300 → results/runs/dev/E2: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/I3 [jsonl] results/runs/dev/I3/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/I3: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- dev/I3r [jsonl] results/runs/dev/I3r/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/I3r: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- dev/I3r2 [jsonl] results/runs/dev/I3r2/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/I3r2: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/I3t [jsonl] results/runs/dev/I3t/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/I3t: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- dev/I3tr [jsonl] results/runs/dev/I3tr/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/I3tr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- dev/I3tr2 [jsonl] results/runs/dev/I3tr2/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/I3tr2: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1a [jsonl] results/runs/dev/M1a/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1a: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин anscorrect/jsonl-ийг ашиглаагүй: src давуу)
- dev/M1a_fx [jsonl] results/runs/dev/M1a_fx/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1a_fx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1a_rs [jsonl] results/runs/dev/M1a_rs/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1a_rs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1a_tzn [jsonl] results/runs/dev/M1a_tzn/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1a_tzn: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1afx [jsonl] results/runs/dev/M1afx/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1afx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ens [jsonl] results/runs/dev/M1ens/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ens: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (байгаа, --force) (төслийн хуучин anscorrect/jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensE2 [jsonl] results/runs/dev/M1ensE2/arctic-r1-7b__mn__s1__h_H4.jsonl n=300 → results/runs/dev/M1ensE2: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensE2r [jsonl] results/runs/dev/M1ensE2r/arctic-r1-7b__mn__s1__h_H4.jsonl n=300 → results/runs/dev/M1ensE2r: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensE2r_r7 [jsonl] results/runs/dev/M1ensE2r_r7/arctic-r1-7b__mn__s1__h_H4.jsonl n=300 → results/runs/dev/M1ensE2r_r7: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensE2rfx_r7 [jsonl] results/runs/dev/M1ensE2rfx_r7/arctic-r1-7b__mn__s1__h_H4.jsonl n=300 → results/runs/dev/M1ensE2rfx_r7: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ens_r7 [jsonl] results/runs/dev/M1ens_r7/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ens_r7: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ens_rs [jsonl] results/runs/dev/M1ens_rs/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ens_rs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ens_tzn [jsonl] results/runs/dev/M1ens_tzn/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ens_tzn: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensfx [jsonl] results/runs/dev/M1ensfx/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensfx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensfxl [jsonl] results/runs/dev/M1ensfxl/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensfxl: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- dev/M1ensfxr [jsonl] results/runs/dev/M1ensfxr/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensfxr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensfxv2 [jsonl] results/runs/dev/M1ensfxv2/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensfxv2: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- dev/M1ensfxv2l [jsonl] results/runs/dev/M1ensfxv2l/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensfxv2l: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- dev/M1ensfxv2lk [jsonl] results/runs/dev/M1ensfxv2lk/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensfxv2lk: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- dev/M1ensp [jsonl] results/runs/dev/M1ensp/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensp: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (байгаа, --force) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensr [jsonl] results/runs/dev/M1ensr/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (байгаа, --force) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensr_rs [jsonl] results/runs/dev/M1ensr_rs/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensr_rs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensra [jsonl] results/runs/dev/M1ensra/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensra: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensx [jsonl] results/runs/dev/M1ensx/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensx0 [jsonl] results/runs/dev/M1ensx0/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensx0: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensxr [jsonl] results/runs/dev/M1ensxr/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensxr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (байгаа, --force), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ensxr0 [jsonl] results/runs/dev/M1ensxr0/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ensxr0: —; алгасав: arctic-r1-7b__mn__s0.jsonl (байгаа, --force), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1fs [jsonl] results/runs/dev/M1fs/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1fs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1g8 [jsonl] results/runs/dev/M1g8/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1g8: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ip [jsonl] results/runs/dev/M1ip/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ip: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1ks [jsonl] results/runs/dev/M1ks/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1ks: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин anscorrect/jsonl-ийг ашиглаагүй: src давуу)
- dev/M1lx [jsonl] results/runs/dev/M1lx/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1lx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1m [jsonl] results/runs/dev/M1m/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1m: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1mr [jsonl] results/runs/dev/M1mr/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1mr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1r [jsonl] results/runs/dev/M1r/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1r: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1rr [jsonl] results/runs/dev/M1rr/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1rr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин anscorrect/jsonl-ийг ашиглаагүй: src давуу)
- dev/M1rt [jsonl] results/runs/dev/M1rt/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1rt: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- dev/M1vf [jsonl] results/runs/dev/M1vf/arctic-r1-7b__mn__s0.jsonl n=300 → results/runs/dev/M1vf: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/E2 [jsonl] results/runs/test/E2/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/E2: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/E3c [jsonl] results/runs/test/E3c/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/E3c: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/E4 [jsonl] results/runs/test/E4/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/E4: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/E4f [jsonl] results/runs/test/E4f/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/E4f: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/H4 [jsonl] results/runs/test/H4/arctic-r1-7b__mn__s0__t4.jsonl n=180 — АНХААР: мөр 180 ≠ 200 — хуулаагүй (--force) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу) — статистик, McNemar, LaTeX-аас ХАСАВ (--force-оор оруулна)
- test/I3 [jsonl] results/runs/test/I3/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/I3: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/I3r [jsonl] results/runs/test/I3r/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/I3r: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/I3r2 [jsonl] results/runs/test/I3r2/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/I3r2: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/I3t [jsonl] results/runs/test/I3t/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/I3t: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/I3tr [jsonl] results/runs/test/I3tr/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/I3tr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/I3tr2 [jsonl] results/runs/test/I3tr2/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/I3tr2: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1a [jsonl] results/runs/test/M1a/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1a: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин anscorrect/jsonl-ийг ашиглаагүй: src давуу)
- test/M1a_fx [jsonl] results/runs/test/M1a_fx/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1a_fx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1a_rs [jsonl] results/runs/test/M1a_rs/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1a_rs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1a_tzn [jsonl] results/runs/test/M1a_tzn/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1a_tzn: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ens [jsonl] results/runs/test/M1ens/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ens: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensE2 [jsonl] results/runs/test/M1ensE2/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/M1ensE2: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensE2r [jsonl] results/runs/test/M1ensE2r/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/M1ensE2r: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensE2r_r7 [jsonl] results/runs/test/M1ensE2r_r7/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/M1ensE2r_r7: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensE2rfx_r7 [jsonl] results/runs/test/M1ensE2rfx_r7/arctic-r1-7b__mn__s1__h_H4.jsonl n=200 → results/runs/test/M1ensE2rfx_r7: —; алгасав: arctic-r1-7b__mn__s1__h_H4.jsonl (ижил), arctic-r1-7b__mn__s1__h_H4.summary.json (ижил), arctic-r1-7b__mn__s1__h_H4.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ens_r7 [jsonl] results/runs/test/M1ens_r7/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ens_r7: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ens_rs [jsonl] results/runs/test/M1ens_rs/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ens_rs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ens_tzn [jsonl] results/runs/test/M1ens_tzn/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ens_tzn: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensfx [jsonl] results/runs/test/M1ensfx/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensfx: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensfxl [jsonl] results/runs/test/M1ensfxl/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensfxl: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- test/M1ensfxr [jsonl] results/runs/test/M1ensfxr/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensfxr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensfxv2 [jsonl] results/runs/test/M1ensfxv2/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensfxv2: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- test/M1ensfxv2l [jsonl] results/runs/test/M1ensfxv2l/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensfxv2l: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- test/M1ensfxv2lk [jsonl] results/runs/test/M1ensfxv2lk/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensfxv2lk: arctic-r1-7b__mn__s0.jsonl, arctic-r1-7b__mn__s0.summary.json, arctic-r1-7b__mn__s0.meta.json
- test/M1ensr [jsonl] results/runs/test/M1ensr/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/M1ensr_rs [jsonl] results/runs/test/M1ensr_rs/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ensr_rs: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин jsonl-ийг ашиглаагүй: src давуу)
- test/M1g8 [jsonl] results/runs/test/M1g8/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1g8: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/M1ks [jsonl] results/runs/test/M1ks/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1ks: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/M1m [jsonl] results/runs/test/M1m/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1m: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/M1r [jsonl] results/runs/test/M1r/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1r: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)
- test/M1rr [jsonl] results/runs/test/M1rr/arctic-r1-7b__mn__s0.jsonl n=200 → results/runs/test/M1rr: —; алгасав: arctic-r1-7b__mn__s0.jsonl (ижил), arctic-r1-7b__mn__s0.summary.json (ижил), arctic-r1-7b__mn__s0.meta.json (ижил) (төслийн хуучин compact/jsonl-ийг ашиглаагүй: src давуу)

## Хасагдсан run-ууд (статистик, McNemar, LaTeX-д ороогүй; --force-оор оруулна)

- test/H4 [jsonl]: мөр 180 ≠ 200

## Test (200)

| ID | Нийт | Wilson 95% CI | Хариулах | Wilson 95% CI | Боломжгүй [Wilson 95% CI] | Хоёрдмол [Wilson 95% CI] | Хэлбэр | Төлөв |
|---|---|---|---|---|---|---|---|---|
| Үргэлж тодруулах (суурь) | 49/200 (24.5%) | 19.1–30.9 | 0/151 (0.0%) | 0.0–2.5 | 24/24 (100.0%) [86.2–100.0] | 25/25 (100.0%) [86.7–100.0] | суурь | суурь (тооцоолсон) |
| I3t | 77/200 (38.5%) | 32.0–45.4 | 62/151 (41.1%) | 33.5–49.0 | 5/24 (20.8%) [9.2–40.5] | 10/25 (40.0%) [23.4–59.3] | jsonl | бүрэн |
| M1a | 101/200 (50.5%) | 43.6–57.4 | 81/151 (53.6%) | 45.7–61.4 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ks | 100/200 (50.0%) | 43.1–56.9 | 82/151 (54.3%) | 46.4–62.0 | 6/24 (25.0%) [12.0–44.9] | 12/25 (48.0%) [30.0–66.5] | jsonl | бүрэн |
| M1rr | 100/200 (50.0%) | 43.1–56.9 | 81/151 (53.6%) | 45.7–61.4 | 8/24 (33.3%) [18.0–53.3] | 11/25 (44.0%) [26.7–62.9] | jsonl | бүрэн |
| M1g8 | 96/200 (48.0%) | 41.2–54.9 | 74/151 (49.0%) | 41.2–56.9 | 8/24 (33.3%) [18.0–53.3] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1m | 102/200 (51.0%) | 44.1–57.8 | 82/151 (54.3%) | 46.4–62.0 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1r | 113/200 (56.5%) | 49.6–63.2 | 78/151 (51.7%) | 43.7–59.5 | 16/24 (66.7%) [46.7–82.0] | 19/25 (76.0%) [56.6–88.5] | jsonl | бүрэн |
| M1ens | 110/200 (55.0%) | 48.1–61.7 | 90/151 (59.6%) | 51.6–67.1 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ensr | 120/200 (60.0%) | 53.1–66.5 | 87/151 (57.6%) | 49.6–65.2 | 14/24 (58.3%) [38.8–75.5] | 19/25 (76.0%) [56.6–88.5] | jsonl | бүрэн |
| E2 | 77/200 (38.5%) | 32.0–45.4 | 62/151 (41.1%) | 33.5–49.0 | 5/24 (20.8%) [9.2–40.5] | 10/25 (40.0%) [23.4–59.3] | jsonl | бүрэн |
| E3c | 93/200 (46.5%) | 39.7–53.4 | 59/151 (39.1%) | 31.7–47.0 | 17/24 (70.8%) [50.8–85.1] | 17/25 (68.0%) [48.4–82.8] | jsonl | бүрэн |
| E4 | 90/200 (45.0%) | 38.3–51.9 | 64/151 (42.4%) | 34.8–50.4 | 10/24 (41.7%) [24.5–61.2] | 16/25 (64.0%) [44.5–79.8] | jsonl | бүрэн |
| E4f | 81/200 (40.5%) | 33.9–47.4 | 65/151 (43.0%) | 35.4–51.0 | 5/24 (20.8%) [9.2–40.5] | 11/25 (44.0%) [26.7–62.9] | jsonl | бүрэн |
| I3 | 75/200 (37.5%) | 31.1–44.4 | 60/151 (39.7%) | 32.3–47.7 | 5/24 (20.8%) [9.2–40.5] | 10/25 (40.0%) [23.4–59.3] | jsonl | бүрэн |
| I3r | 88/200 (44.0%) | 37.3–50.9 | 58/151 (38.4%) | 31.0–46.4 | 15/24 (62.5%) [42.7–78.8] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| I3r2 | 88/200 (44.0%) | 37.3–50.9 | 58/151 (38.4%) | 31.0–46.4 | 15/24 (62.5%) [42.7–78.8] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| I3tr | 90/200 (45.0%) | 38.3–51.9 | 60/151 (39.7%) | 32.3–47.7 | 15/24 (62.5%) [42.7–78.8] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| I3tr2 | 90/200 (45.0%) | 38.3–51.9 | 60/151 (39.7%) | 32.3–47.7 | 15/24 (62.5%) [42.7–78.8] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| M1a_fx | 108/200 (54.0%) | 47.1–60.8 | 87/151 (57.6%) | 49.6–65.2 | 6/24 (25.0%) [12.0–44.9] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| M1a_rs | 101/200 (50.5%) | 43.6–57.4 | 81/151 (53.6%) | 45.7–61.4 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1a_tzn | 101/200 (50.5%) | 43.6–57.4 | 81/151 (53.6%) | 45.7–61.4 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ensE2 | 109/200 (54.5%) | 47.6–61.3 | 90/151 (59.6%) | 51.6–67.1 | 5/24 (20.8%) [9.2–40.5] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ensE2r | 121/200 (60.5%) | 53.6–67.0 | 88/151 (58.3%) | 50.3–65.8 | 14/24 (58.3%) [38.8–75.5] | 19/25 (76.0%) [56.6–88.5] | jsonl | бүрэн |
| M1ensE2r_r7 | 121/200 (60.5%) | 53.6–67.0 | 88/151 (58.3%) | 50.3–65.8 | 14/24 (58.3%) [38.8–75.5] | 19/25 (76.0%) [56.6–88.5] | jsonl | бүрэн |
| M1ensE2rfx_r7 | 123/200 (61.5%) | 54.6–68.0 | 90/151 (59.6%) | 51.6–67.1 | 13/24 (54.2%) [35.1–72.1] | 20/25 (80.0%) [60.9–91.1] | jsonl | бүрэн |
| M1ens_r7 | 110/200 (55.0%) | 48.1–61.7 | 90/151 (59.6%) | 51.6–67.1 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ens_rs | 110/200 (55.0%) | 48.1–61.7 | 90/151 (59.6%) | 51.6–67.1 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ens_tzn | 110/200 (55.0%) | 48.1–61.7 | 90/151 (59.6%) | 51.6–67.1 | 6/24 (25.0%) [12.0–44.9] | 14/25 (56.0%) [37.1–73.3] | jsonl | бүрэн |
| M1ensfx | 116/200 (58.0%) | 51.1–64.6 | 95/151 (62.9%) | 55.0–70.2 | 6/24 (25.0%) [12.0–44.9] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| M1ensfxl | 120/200 (60.0%) | 53.1–66.5 | 99/151 (65.6%) | 57.7–72.7 | 6/24 (25.0%) [12.0–44.9] | 15/25 (60.0%) [40.7–76.6] | jsonl | бүрэн |
| M1ensfxr | 125/200 (62.5%) | 55.6–68.9 | 92/151 (60.9%) | 53.0–68.3 | 13/24 (54.2%) [35.1–72.1] | 20/25 (80.0%) [60.9–91.1] | jsonl | бүрэн |
| M1ensfxv2 | 143/200 (71.5%) | 64.9–77.3 | 95/151 (62.9%) | 55.0–70.2 | 24/24 (100.0%) [86.2–100.0] | 24/25 (96.0%) [80.5–99.3] | jsonl | бүрэн |
| M1ensfxv2l | 147/200 (73.5%) | 67.0–79.1 | 99/151 (65.6%) | 57.7–72.7 | 24/24 (100.0%) [86.2–100.0] | 24/25 (96.0%) [80.5–99.3] | jsonl | бүрэн |
| M1ensfxv2lk | 156/200 (78.0%) | 71.8–83.2 | 108/151 (71.5%) | 63.9–78.1 | 24/24 (100.0%) [86.2–100.0] | 24/25 (96.0%) [80.5–99.3] | jsonl | бүрэн |
| M1ensr_rs | 120/200 (60.0%) | 53.1–66.5 | 87/151 (57.6%) | 49.6–65.2 | 14/24 (58.3%) [38.8–75.5] | 19/25 (76.0%) [56.6–88.5] | jsonl | бүрэн |

_post hoc (test дээр хамгийн өндрийг сонгосон — headline биш, нотолгоо биш): M1ensfxv2lk нийт 156/200 (78.0%; Wilson 95% CI 71.8–83.2)_

**Түвшин (хариулах, EX% (k/n) [Wilson 95% CI])**

| ID | easy | medium | hard |
|---|---|---|---|
| I3t | 52.6 (20/38) [37.3–67.5] | 40.2 (33/82) [30.3–51.1] | 29.0 (9/31) [16.1–46.6] |
| M1a | 68.4 (26/38) [52.5–80.9] | 53.7 (44/82) [42.9–64.0] | 35.5 (11/31) [21.1–53.1] |
| M1ks | 65.8 (25/38) [49.9–78.8] | 57.3 (47/82) [46.5–67.5] | 32.3 (10/31) [18.6–49.9] |
| M1rr | 68.4 (26/38) [52.5–80.9] | 57.3 (47/82) [46.5–67.5] | 25.8 (8/31) [13.7–43.2] |
| M1g8 | 68.4 (26/38) [52.5–80.9] | 47.6 (39/82) [37.1–58.2] | 29.0 (9/31) [16.1–46.6] |
| M1m | 68.4 (26/38) [52.5–80.9] | 54.9 (45/82) [44.1–65.2] | 35.5 (11/31) [21.1–53.1] |
| M1r | 68.4 (26/38) [52.5–80.9] | 52.4 (43/82) [41.8–62.9] | 29.0 (9/31) [16.1–46.6] |
| M1ens | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 35.5 (11/31) [21.1–53.1] |
| M1ensr | 76.3 (29/38) [60.8–87.0] | 59.8 (49/82) [48.9–69.7] | 29.0 (9/31) [16.1–46.6] |
| E2 | 57.9 (22/38) [42.2–72.1] | 36.6 (30/82) [27.0–47.4] | 32.3 (10/31) [18.6–49.9] |
| E3c | 55.3 (21/38) [39.7–69.9] | 35.4 (29/82) [25.9–46.2] | 29.0 (9/31) [16.1–46.6] |
| E4 | 60.5 (23/38) [44.7–74.4] | 39.0 (32/82) [29.2–49.8] | 29.0 (9/31) [16.1–46.6] |
| E4f | 60.5 (23/38) [44.7–74.4] | 39.0 (32/82) [29.2–49.8] | 32.3 (10/31) [18.6–49.9] |
| I3 | 52.6 (20/38) [37.3–67.5] | 37.8 (31/82) [28.1–48.6] | 29.0 (9/31) [16.1–46.6] |
| I3r | 52.6 (20/38) [37.3–67.5] | 37.8 (31/82) [28.1–48.6] | 22.6 (7/31) [11.4–39.8] |
| I3r2 | 52.6 (20/38) [37.3–67.5] | 37.8 (31/82) [28.1–48.6] | 22.6 (7/31) [11.4–39.8] |
| I3tr | 52.6 (20/38) [37.3–67.5] | 40.2 (33/82) [30.3–51.1] | 22.6 (7/31) [11.4–39.8] |
| I3tr2 | 52.6 (20/38) [37.3–67.5] | 40.2 (33/82) [30.3–51.1] | 22.6 (7/31) [11.4–39.8] |
| M1a_fx | 73.7 (28/38) [58.0–85.0] | 58.5 (48/82) [47.7–68.6] | 35.5 (11/31) [21.1–53.1] |
| M1a_rs | 68.4 (26/38) [52.5–80.9] | 53.7 (44/82) [42.9–64.0] | 35.5 (11/31) [21.1–53.1] |
| M1a_tzn | 68.4 (26/38) [52.5–80.9] | 53.7 (44/82) [42.9–64.0] | 35.5 (11/31) [21.1–53.1] |
| M1ensE2 | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 35.5 (11/31) [21.1–53.1] |
| M1ensE2r | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 29.0 (9/31) [16.1–46.6] |
| M1ensE2r_r7 | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 29.0 (9/31) [16.1–46.6] |
| M1ensE2rfx_r7 | 76.3 (29/38) [60.8–87.0] | 63.4 (52/82) [52.6–73.0] | 29.0 (9/31) [16.1–46.6] |
| M1ens_r7 | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 35.5 (11/31) [21.1–53.1] |
| M1ens_rs | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 35.5 (11/31) [21.1–53.1] |
| M1ens_tzn | 76.3 (29/38) [60.8–87.0] | 61.0 (50/82) [50.2–70.8] | 35.5 (11/31) [21.1–53.1] |
| M1ensfx | 78.9 (30/38) [63.7–88.9] | 65.9 (54/82) [55.1–75.2] | 35.5 (11/31) [21.1–53.1] |
| M1ensfxl | 78.9 (30/38) [63.7–88.9] | 70.7 (58/82) [60.1–79.5] | 35.5 (11/31) [21.1–53.1] |
| M1ensfxr | 78.9 (30/38) [63.7–88.9] | 64.6 (53/82) [53.8–74.1] | 29.0 (9/31) [16.1–46.6] |
| M1ensfxv2 | 78.9 (30/38) [63.7–88.9] | 65.9 (54/82) [55.1–75.2] | 35.5 (11/31) [21.1–53.1] |
| M1ensfxv2l | 78.9 (30/38) [63.7–88.9] | 70.7 (58/82) [60.1–79.5] | 35.5 (11/31) [21.1–53.1] |
| M1ensfxv2lk | 81.6 (31/38) [66.6–90.8] | 79.3 (65/82) [69.3–86.6] | 38.7 (12/31) [23.7–56.2] |
| M1ensr_rs | 76.3 (29/38) [60.8–87.0] | 59.8 (49/82) [48.9–69.7] | 29.0 (9/31) [16.1–46.6] |

**Домэйн (хариулах, EX% (k/n) [Wilson 95% CI])**

| ID | applications | collection | cross | customers | deposits | gl_trends | loans | payments |
|---|---|---|---|---|---|---|---|---|
| I3t | 47.4 (9/19) [27.3–68.3] | 31.6 (6/19) [15.4–54.0] | 22.2 (4/18) [9.0–45.2] | 36.8 (7/19) [19.1–59.0] | 47.4 (9/19) [27.3–68.3] | 38.9 (7/18) [20.3–61.4] | 55.0 (11/20) [34.2–74.2] | 47.4 (9/19) [27.3–68.3] |
| M1a | 47.4 (9/19) [27.3–68.3] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 47.4 (9/19) [27.3–68.3] |
| M1ks | 47.4 (9/19) [27.3–68.3] | 42.1 (8/19) [23.1–63.7] | 22.2 (4/18) [9.0–45.2] | 63.2 (12/19) [41.0–80.9] | 68.4 (13/19) [46.0–84.6] | 77.8 (14/18) [54.8–91.0] | 65.0 (13/20) [43.3–81.9] | 47.4 (9/19) [27.3–68.3] |
| M1rr | 52.6 (10/19) [31.7–72.7] | 47.4 (9/19) [27.3–68.3] | 27.8 (5/18) [12.5–50.9] | 57.9 (11/19) [36.3–76.9] | 42.1 (8/19) [23.1–63.7] | 72.2 (13/18) [49.1–87.5] | 85.0 (17/20) [64.0–94.8] | 42.1 (8/19) [23.1–63.7] |
| M1g8 | 36.8 (7/19) [19.1–59.0] | 36.8 (7/19) [19.1–59.0] | 5.6 (1/18) [1.0–25.8] | 52.6 (10/19) [31.7–72.7] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 75.0 (15/20) [53.1–88.8] | 47.4 (9/19) [27.3–68.3] |
| M1m | 47.4 (9/19) [27.3–68.3] | 47.4 (9/19) [27.3–68.3] | 16.7 (3/18) [5.8–39.2] | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 47.4 (9/19) [27.3–68.3] |
| M1r | 47.4 (9/19) [27.3–68.3] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 52.6 (10/19) [31.7–72.7] | 52.6 (10/19) [31.7–72.7] | 77.8 (14/18) [54.8–91.0] | 75.0 (15/20) [53.1–88.8] | 47.4 (9/19) [27.3–68.3] |
| M1ens | 47.4 (9/19) [27.3–68.3] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 57.9 (11/19) [36.3–76.9] |
| M1ensr | 47.4 (9/19) [27.3–68.3] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 57.9 (11/19) [36.3–76.9] | 52.6 (10/19) [31.7–72.7] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 57.9 (11/19) [36.3–76.9] |
| E2 | 52.6 (10/19) [31.7–72.7] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 42.1 (8/19) [23.1–63.7] | 42.1 (8/19) [23.1–63.7] | 38.9 (7/18) [20.3–61.4] | 50.0 (10/20) [29.9–70.1] | 42.1 (8/19) [23.1–63.7] |
| E3c | 47.4 (9/19) [27.3–68.3] | 42.1 (8/19) [23.1–63.7] | 5.6 (1/18) [1.0–25.8] | 42.1 (8/19) [23.1–63.7] | 42.1 (8/19) [23.1–63.7] | 38.9 (7/18) [20.3–61.4] | 50.0 (10/20) [29.9–70.1] | 42.1 (8/19) [23.1–63.7] |
| E4 | 57.9 (11/19) [36.3–76.9] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 42.1 (8/19) [23.1–63.7] | 42.1 (8/19) [23.1–63.7] | 44.4 (8/18) [24.6–66.3] | 45.0 (9/20) [25.8–65.8] | 47.4 (9/19) [27.3–68.3] |
| E4f | 57.9 (11/19) [36.3–76.9] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 42.1 (8/19) [23.1–63.7] | 42.1 (8/19) [23.1–63.7] | 44.4 (8/18) [24.6–66.3] | 50.0 (10/20) [29.9–70.1] | 47.4 (9/19) [27.3–68.3] |
| I3 | 36.8 (7/19) [19.1–59.0] | 31.6 (6/19) [15.4–54.0] | 11.1 (2/18) [3.1–32.8] | 36.8 (7/19) [19.1–59.0] | 47.4 (9/19) [27.3–68.3] | 44.4 (8/18) [24.6–66.3] | 55.0 (11/20) [34.2–74.2] | 52.6 (10/19) [31.7–72.7] |
| I3r | 36.8 (7/19) [19.1–59.0] | 31.6 (6/19) [15.4–54.0] | 11.1 (2/18) [3.1–32.8] | 31.6 (6/19) [15.4–54.0] | 47.4 (9/19) [27.3–68.3] | 44.4 (8/18) [24.6–66.3] | 50.0 (10/20) [29.9–70.1] | 52.6 (10/19) [31.7–72.7] |
| I3r2 | 36.8 (7/19) [19.1–59.0] | 31.6 (6/19) [15.4–54.0] | 11.1 (2/18) [3.1–32.8] | 31.6 (6/19) [15.4–54.0] | 47.4 (9/19) [27.3–68.3] | 44.4 (8/18) [24.6–66.3] | 50.0 (10/20) [29.9–70.1] | 52.6 (10/19) [31.7–72.7] |
| I3tr | 47.4 (9/19) [27.3–68.3] | 31.6 (6/19) [15.4–54.0] | 22.2 (4/18) [9.0–45.2] | 31.6 (6/19) [15.4–54.0] | 47.4 (9/19) [27.3–68.3] | 38.9 (7/18) [20.3–61.4] | 50.0 (10/20) [29.9–70.1] | 47.4 (9/19) [27.3–68.3] |
| I3tr2 | 47.4 (9/19) [27.3–68.3] | 31.6 (6/19) [15.4–54.0] | 22.2 (4/18) [9.0–45.2] | 31.6 (6/19) [15.4–54.0] | 47.4 (9/19) [27.3–68.3] | 38.9 (7/18) [20.3–61.4] | 50.0 (10/20) [29.9–70.1] | 47.4 (9/19) [27.3–68.3] |
| M1a_fx | 57.9 (11/19) [36.3–76.9] | 52.6 (10/19) [31.7–72.7] | 11.1 (2/18) [3.1–32.8] | 68.4 (13/19) [46.0–84.6] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 47.4 (9/19) [27.3–68.3] |
| M1a_rs | 47.4 (9/19) [27.3–68.3] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 47.4 (9/19) [27.3–68.3] |
| M1a_tzn | 47.4 (9/19) [27.3–68.3] | 47.4 (9/19) [27.3–68.3] | 11.1 (2/18) [3.1–32.8] | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 47.4 (9/19) [27.3–68.3] |
| M1ensE2 | 63.2 (12/19) [41.0–80.9] | 47.4 (9/19) [27.3–68.3] | 27.8 (5/18) [12.5–50.9] | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 57.9 (11/19) [36.3–76.9] |
| M1ensE2r | 63.2 (12/19) [41.0–80.9] | 47.4 (9/19) [27.3–68.3] | 27.8 (5/18) [12.5–50.9] | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 75.0 (15/20) [53.1–88.8] | 57.9 (11/19) [36.3–76.9] |
| M1ensE2r_r7 | 63.2 (12/19) [41.0–80.9] | 47.4 (9/19) [27.3–68.3] | 27.8 (5/18) [12.5–50.9] | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 75.0 (15/20) [53.1–88.8] | 57.9 (11/19) [36.3–76.9] |
| M1ensE2rfx_r7 | 63.2 (12/19) [41.0–80.9] | 47.4 (9/19) [27.3–68.3] | 27.8 (5/18) [12.5–50.9] | 68.4 (13/19) [46.0–84.6] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 75.0 (15/20) [53.1–88.8] | 57.9 (11/19) [36.3–76.9] |
| M1ens_r7 | 47.4 (9/19) [27.3–68.3] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 57.9 (11/19) [36.3–76.9] |
| M1ens_rs | 47.4 (9/19) [27.3–68.3] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 57.9 (11/19) [36.3–76.9] |
| M1ens_tzn | 47.4 (9/19) [27.3–68.3] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 57.9 (11/19) [36.3–76.9] |
| M1ensfx | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 73.7 (14/19) [51.2–88.2] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 63.2 (12/19) [41.0–80.9] |
| M1ensfxl | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 38.9 (7/18) [20.3–61.4] | 73.7 (14/19) [51.2–88.2] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 90.0 (18/20) [69.9–97.2] | 63.2 (12/19) [41.0–80.9] |
| M1ensfxr | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 68.4 (13/19) [46.0–84.6] | 52.6 (10/19) [31.7–72.7] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 63.2 (12/19) [41.0–80.9] |
| M1ensfxv2 | 57.9 (11/19) [36.3–76.9] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 73.7 (14/19) [51.2–88.2] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 85.0 (17/20) [64.0–94.8] | 63.2 (12/19) [41.0–80.9] |
| M1ensfxv2l | 63.2 (12/19) [41.0–80.9] | 57.9 (11/19) [36.3–76.9] | 38.9 (7/18) [20.3–61.4] | 73.7 (14/19) [51.2–88.2] | 57.9 (11/19) [36.3–76.9] | 77.8 (14/18) [54.8–91.0] | 90.0 (18/20) [69.9–97.2] | 63.2 (12/19) [41.0–80.9] |
| M1ensfxv2lk | 78.9 (15/19) [56.7–91.5] | 63.2 (12/19) [41.0–80.9] | 50.0 (9/18) [29.0–71.0] | 73.7 (14/19) [51.2–88.2] | 63.2 (12/19) [41.0–80.9] | 77.8 (14/18) [54.8–91.0] | 90.0 (18/20) [69.9–97.2] | 73.7 (14/19) [51.2–88.2] |
| M1ensr_rs | 47.4 (9/19) [27.3–68.3] | 57.9 (11/19) [36.3–76.9] | 27.8 (5/18) [12.5–50.9] | 57.9 (11/19) [36.3–76.9] | 52.6 (10/19) [31.7–72.7] | 77.8 (14/18) [54.8–91.0] | 80.0 (16/20) [58.4–91.9] | 57.9 (11/19) [36.3–76.9] |

## Dev (300)

| ID | Нийт | Wilson 95% CI | Хариулах | Wilson 95% CI | Боломжгүй [Wilson 95% CI] | Хоёрдмол [Wilson 95% CI] | Хэлбэр | Төлөв |
|---|---|---|---|---|---|---|---|---|
| Үргэлж тодруулах (суурь) | 76/300 (25.3%) | 20.7–30.5 | 0/224 (0.0%) | 0.0–1.7 | 38/38 (100.0%) [90.8–100.0] | 38/38 (100.0%) [90.8–100.0] | суурь | суурь (тооцоолсон) |
| M1a | 160/300 (53.3%) | 47.7–58.9 | 127/224 (56.7%) | 50.1–63.0 | 17/38 (44.7%) [30.1–60.3] | 16/38 (42.1%) [27.9–57.8] | jsonl | бүрэн |
| M1ks | 165/300 (55.0%) | 49.3–60.5 | 130/224 (58.0%) | 51.5–64.3 | 17/38 (44.7%) [30.1–60.3] | 18/38 (47.4%) [32.5–62.7] | jsonl | бүрэн |
| M1rr | 164/300 (54.7%) | 49.0–60.2 | 131/224 (58.5%) | 51.9–64.7 | 16/38 (42.1%) [27.9–57.8] | 17/38 (44.7%) [30.1–60.3] | jsonl | бүрэн |
| M1g8 | 158/300 (52.7%) | 47.0–58.2 | 126/224 (56.2%) | 49.7–62.6 | 17/38 (44.7%) [30.1–60.3] | 15/38 (39.5%) [25.6–55.3] | jsonl | бүрэн |
| M1m | 161/300 (53.7%) | 48.0–59.2 | 129/224 (57.6%) | 51.0–63.9 | 17/38 (44.7%) [30.1–60.3] | 15/38 (39.5%) [25.6–55.3] | jsonl | бүрэн |
| M1ens | 176/300 (58.7%) | 53.0–64.1 | 137/224 (61.2%) | 54.6–67.3 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensr | 197/300 (65.7%) | 60.1–70.8 | 137/224 (61.2%) | 54.6–67.3 | 31/38 (81.6%) [66.6–90.8] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ensp | 176/300 (58.7%) | 53.0–64.1 | 137/224 (61.2%) | 54.6–67.3 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| A1 | 43/300 (14.3%) | 10.8–18.8 | 37/224 (16.5%) | 12.2–21.9 | 2/38 (5.3%) [1.5–17.3] | 4/38 (10.5%) [4.2–24.1] | jsonl | бүрэн |
| A6 | 47/300 (15.7%) | 12.0–20.2 | 42/224 (18.8%) | 14.2–24.4 | 0/38 (0.0%) [0.0–9.2] | 5/38 (13.2%) [5.8–27.3] | jsonl | бүрэн |
| A7 | 64/300 (21.3%) | 17.1–26.3 | 54/224 (24.1%) | 19.0–30.1 | 2/38 (5.3%) [1.5–17.3] | 8/38 (21.1%) [11.1–36.3] | jsonl | бүрэн |
| C1 | 9/300 (3.0%) | 1.6–5.6 | 7/224 (3.1%) | 1.5–6.3 | 0/38 (0.0%) [0.0–9.2] | 2/38 (5.3%) [1.5–17.3] | jsonl | бүрэн |
| C2 | 9/300 (3.0%) | 1.6–5.6 | 7/224 (3.1%) | 1.5–6.3 | 0/38 (0.0%) [0.0–9.2] | 2/38 (5.3%) [1.5–17.3] | jsonl | бүрэн |
| C3 | 29/300 (9.7%) | 6.8–13.5 | 24/224 (10.7%) | 7.3–15.4 | 1/38 (2.6%) [0.5–13.5] | 4/38 (10.5%) [4.2–24.1] | jsonl | бүрэн |
| E2 | 123/300 (41.0%) | 35.6–46.6 | 100/224 (44.6%) | 38.3–51.2 | 13/38 (34.2%) [21.2–50.1] | 10/38 (26.3%) [15.0–42.0] | jsonl | бүрэн |
| I3 | 123/300 (41.0%) | 35.6–46.6 | 97/224 (43.3%) | 37.0–49.9 | 16/38 (42.1%) [27.9–57.8] | 10/38 (26.3%) [15.0–42.0] | jsonl | бүрэн |
| I3r | 148/300 (49.3%) | 43.7–55.0 | 97/224 (43.3%) | 37.0–49.9 | 34/38 (89.5%) [75.9–95.8] | 17/38 (44.7%) [30.1–60.3] | jsonl | бүрэн |
| I3r2 | 148/300 (49.3%) | 43.7–55.0 | 97/224 (43.3%) | 37.0–49.9 | 34/38 (89.5%) [75.9–95.8] | 17/38 (44.7%) [30.1–60.3] | jsonl | бүрэн |
| I3t | 132/300 (44.0%) | 38.5–49.7 | 105/224 (46.9%) | 40.4–53.4 | 17/38 (44.7%) [30.1–60.3] | 10/38 (26.3%) [15.0–42.0] | jsonl | бүрэн |
| I3tr | 157/300 (52.3%) | 46.7–57.9 | 105/224 (46.9%) | 40.4–53.4 | 34/38 (89.5%) [75.9–95.8] | 18/38 (47.4%) [32.5–62.7] | jsonl | бүрэн |
| I3tr2 | 157/300 (52.3%) | 46.7–57.9 | 105/224 (46.9%) | 40.4–53.4 | 34/38 (89.5%) [75.9–95.8] | 18/38 (47.4%) [32.5–62.7] | jsonl | бүрэн |
| M1a_fx | 172/300 (57.3%) | 51.7–62.8 | 138/224 (61.6%) | 55.1–67.7 | 17/38 (44.7%) [30.1–60.3] | 17/38 (44.7%) [30.1–60.3] | jsonl | бүрэн |
| M1a_rs | 160/300 (53.3%) | 47.7–58.9 | 127/224 (56.7%) | 50.1–63.0 | 17/38 (44.7%) [30.1–60.3] | 16/38 (42.1%) [27.9–57.8] | jsonl | бүрэн |
| M1a_tzn | 163/300 (54.3%) | 48.7–59.9 | 130/224 (58.0%) | 51.5–64.3 | 17/38 (44.7%) [30.1–60.3] | 16/38 (42.1%) [27.9–57.8] | jsonl | бүрэн |
| M1afx | 171/300 (57.0%) | 51.3–62.5 | 138/224 (61.6%) | 55.1–67.7 | 17/38 (44.7%) [30.1–60.3] | 16/38 (42.1%) [27.9–57.8] | jsonl | бүрэн |
| M1ensE2 | 186/300 (62.0%) | 56.4–67.3 | 151/224 (67.4%) | 61.0–73.2 | 14/38 (36.8%) [23.4–52.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensE2r | 209/300 (69.7%) | 64.2–74.6 | 151/224 (67.4%) | 61.0–73.2 | 29/38 (76.3%) [60.8–87.0] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ensE2r_r7 | 212/300 (70.7%) | 65.3–75.5 | 154/224 (68.8%) | 62.4–74.5 | 29/38 (76.3%) [60.8–87.0] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ensE2rfx_r7 | 215/300 (71.7%) | 66.3–76.5 | 157/224 (70.1%) | 63.8–75.7 | 29/38 (76.3%) [60.8–87.0] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ens_r7 | 179/300 (59.7%) | 54.0–65.1 | 140/224 (62.5%) | 56.0–68.6 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ens_rs | 176/300 (58.7%) | 53.0–64.1 | 137/224 (61.2%) | 54.6–67.3 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ens_tzn | 179/300 (59.7%) | 54.0–65.1 | 140/224 (62.5%) | 56.0–68.6 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensfx | 185/300 (61.7%) | 56.1–67.0 | 146/224 (65.2%) | 58.7–71.1 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensfxl | 190/300 (63.3%) | 57.7–68.6 | 151/224 (67.4%) | 61.0–73.2 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensfxr | 206/300 (68.7%) | 63.2–73.7 | 146/224 (65.2%) | 58.7–71.1 | 31/38 (81.6%) [66.6–90.8] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ensfxv2 | 217/300 (72.3%) | 67.0–77.1 | 146/224 (65.2%) | 58.7–71.1 | 38/38 (100.0%) [90.8–100.0] | 33/38 (86.8%) [72.7–94.2] | jsonl | бүрэн |
| M1ensfxv2l | 222/300 (74.0%) | 68.8–78.6 | 151/224 (67.4%) | 61.0–73.2 | 38/38 (100.0%) [90.8–100.0] | 33/38 (86.8%) [72.7–94.2] | jsonl | бүрэн |
| M1ensfxv2lk | 241/300 (80.3%) | 75.5–84.4 | 170/224 (75.9%) | 69.9–81.0 | 38/38 (100.0%) [90.8–100.0] | 33/38 (86.8%) [72.7–94.2] | jsonl | бүрэн |
| M1ensr_rs | 197/300 (65.7%) | 60.1–70.8 | 137/224 (61.2%) | 54.6–67.3 | 31/38 (81.6%) [66.6–90.8] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ensra | 201/300 (67.0%) | 61.5–72.1 | 132/224 (58.9%) | 52.4–65.2 | 36/38 (94.7%) [82.7–98.5] | 33/38 (86.8%) [72.7–94.2] | jsonl | бүрэн |
| M1ensx | 183/300 (61.0%) | 55.4–66.3 | 145/224 (64.7%) | 58.3–70.7 | 17/38 (44.7%) [30.1–60.3] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensx0 | 176/300 (58.7%) | 53.0–64.1 | 137/224 (61.2%) | 54.6–67.3 | 18/38 (47.4%) [32.5–62.7] | 21/38 (55.3%) [39.7–69.9] | jsonl | бүрэн |
| M1ensxr | 204/300 (68.0%) | 62.5–73.0 | 145/224 (64.7%) | 58.3–70.7 | 30/38 (78.9%) [63.7–88.9] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1ensxr0 | 197/300 (65.7%) | 60.1–70.8 | 137/224 (61.2%) | 54.6–67.3 | 31/38 (81.6%) [66.6–90.8] | 29/38 (76.3%) [60.8–87.0] | jsonl | бүрэн |
| M1fs | 158/300 (52.7%) | 47.0–58.2 | 124/224 (55.4%) | 48.8–61.7 | 16/38 (42.1%) [27.9–57.8] | 18/38 (47.4%) [32.5–62.7] | jsonl | бүрэн |
| M1ip | 164/300 (54.7%) | 49.0–60.2 | 129/224 (57.6%) | 51.0–63.9 | 19/38 (50.0%) [34.8–65.2] | 16/38 (42.1%) [27.9–57.8] | jsonl | бүрэн |
| M1lx | 160/300 (53.3%) | 47.7–58.9 | 128/224 (57.1%) | 50.6–63.4 | 17/38 (44.7%) [30.1–60.3] | 15/38 (39.5%) [25.6–55.3] | jsonl | бүрэн |
| M1mr | 186/300 (62.0%) | 56.4–67.3 | 129/224 (57.6%) | 51.0–63.9 | 34/38 (89.5%) [75.9–95.8] | 23/38 (60.5%) [44.7–74.4] | jsonl | бүрэн |
| M1r | 184/300 (61.3%) | 55.7–66.7 | 127/224 (56.7%) | 50.1–63.0 | 33/38 (86.8%) [72.7–94.2] | 24/38 (63.2%) [47.3–76.6] | jsonl | бүрэн |
| M1rt | 161/300 (53.7%) | 48.0–59.2 | 129/224 (57.6%) | 51.0–63.9 | 17/38 (44.7%) [30.1–60.3] | 15/38 (39.5%) [25.6–55.3] | jsonl | бүрэн |
| M1vf | 167/300 (55.7%) | 50.0–61.2 | 132/224 (58.9%) | 52.4–65.2 | 20/38 (52.6%) [37.3–67.5] | 15/38 (39.5%) [25.6–55.3] | jsonl | бүрэн |

**Түвшин (хариулах, EX% (k/n) [Wilson 95% CI])**

| ID | easy | medium | hard |
|---|---|---|---|
| M1a | 68.4 (39/57) [55.5–79.0] | 62.8 (76/121) [53.9–70.9] | 26.1 (12/46) [15.6–40.3] |
| M1ks | 71.9 (41/57) [59.2–81.9] | 62.0 (75/121) [53.1–70.1] | 30.4 (14/46) [19.1–44.8] |
| M1rr | 75.4 (43/57) [62.9–84.8] | 60.3 (73/121) [51.4–68.6] | 32.6 (15/46) [20.9–47.0] |
| M1g8 | 73.7 (42/57) [61.0–83.4] | 58.7 (71/121) [49.8–67.1] | 28.3 (13/46) [17.3–42.5] |
| M1m | 70.2 (40/57) [57.3–80.5] | 63.6 (77/121) [54.8–71.7] | 26.1 (12/46) [15.6–40.3] |
| M1ens | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| M1ensr | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| M1ensp | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| A1 | 29.8 (17/57) [19.5–42.7] | 14.9 (18/121) [9.6–22.3] | 4.3 (2/46) [1.2–14.5] |
| A6 | 33.3 (19/57) [22.5–46.3] | 17.4 (21/121) [11.6–25.1] | 4.3 (2/46) [1.2–14.5] |
| A7 | 36.8 (21/57) [25.5–49.8] | 22.3 (27/121) [15.8–30.5] | 13.0 (6/46) [6.1–25.7] |
| C1 | 7.0 (4/57) [2.8–16.7] | 2.5 (3/121) [0.8–7.0] | 0.0 (0/46) [0.0–7.7] |
| C2 | 8.8 (5/57) [3.8–18.9] | 1.7 (2/121) [0.5–5.8] | 0.0 (0/46) [0.0–7.7] |
| C3 | 19.3 (11/57) [11.1–31.3] | 10.7 (13/121) [6.4–17.5] | 0.0 (0/46) [0.0–7.7] |
| E2 | 64.9 (37/57) [51.9–76.0] | 41.3 (50/121) [32.9–50.2] | 28.3 (13/46) [17.3–42.5] |
| I3 | 59.6 (34/57) [46.7–71.4] | 43.0 (52/121) [34.5–51.9] | 23.9 (11/46) [13.9–37.9] |
| I3r | 59.6 (34/57) [46.7–71.4] | 43.0 (52/121) [34.5–51.9] | 23.9 (11/46) [13.9–37.9] |
| I3r2 | 59.6 (34/57) [46.7–71.4] | 43.0 (52/121) [34.5–51.9] | 23.9 (11/46) [13.9–37.9] |
| I3t | 63.2 (36/57) [50.2–74.5] | 47.9 (58/121) [39.2–56.8] | 23.9 (11/46) [13.9–37.9] |
| I3tr | 63.2 (36/57) [50.2–74.5] | 47.9 (58/121) [39.2–56.8] | 23.9 (11/46) [13.9–37.9] |
| I3tr2 | 63.2 (36/57) [50.2–74.5] | 47.9 (58/121) [39.2–56.8] | 23.9 (11/46) [13.9–37.9] |
| M1a_fx | 75.4 (43/57) [62.9–84.8] | 66.9 (81/121) [58.2–74.7] | 30.4 (14/46) [19.1–44.8] |
| M1a_rs | 68.4 (39/57) [55.5–79.0] | 62.8 (76/121) [53.9–70.9] | 26.1 (12/46) [15.6–40.3] |
| M1a_tzn | 68.4 (39/57) [55.5–79.0] | 63.6 (77/121) [54.8–71.7] | 30.4 (14/46) [19.1–44.8] |
| M1afx | 75.4 (43/57) [62.9–84.8] | 66.9 (81/121) [58.2–74.7] | 30.4 (14/46) [19.1–44.8] |
| M1ensE2 | 82.5 (47/57) [70.6–90.2] | 71.9 (87/121) [63.3–79.1] | 37.0 (17/46) [24.5–51.4] |
| M1ensE2r | 82.5 (47/57) [70.6–90.2] | 71.9 (87/121) [63.3–79.1] | 37.0 (17/46) [24.5–51.4] |
| M1ensE2r_r7 | 82.5 (47/57) [70.6–90.2] | 72.7 (88/121) [64.2–79.9] | 41.3 (19/46) [28.3–55.7] |
| M1ensE2rfx_r7 | 82.5 (47/57) [70.6–90.2] | 74.4 (90/121) [65.9–81.3] | 43.5 (20/46) [30.2–57.8] |
| M1ens_r7 | 75.4 (43/57) [62.9–84.8] | 66.1 (80/121) [57.3–73.9] | 37.0 (17/46) [24.5–51.4] |
| M1ens_rs | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| M1ens_tzn | 75.4 (43/57) [62.9–84.8] | 66.1 (80/121) [57.3–73.9] | 37.0 (17/46) [24.5–51.4] |
| M1ensfx | 77.2 (44/57) [64.8–86.2] | 70.2 (85/121) [61.6–77.7] | 37.0 (17/46) [24.5–51.4] |
| M1ensfxl | 80.7 (46/57) [68.7–88.9] | 71.9 (87/121) [63.3–79.1] | 39.1 (18/46) [26.4–53.5] |
| M1ensfxr | 77.2 (44/57) [64.8–86.2] | 70.2 (85/121) [61.6–77.7] | 37.0 (17/46) [24.5–51.4] |
| M1ensfxv2 | 77.2 (44/57) [64.8–86.2] | 70.2 (85/121) [61.6–77.7] | 37.0 (17/46) [24.5–51.4] |
| M1ensfxv2l | 80.7 (46/57) [68.7–88.9] | 71.9 (87/121) [63.3–79.1] | 39.1 (18/46) [26.4–53.5] |
| M1ensfxv2lk | 84.2 (48/57) [72.6–91.5] | 81.0 (98/121) [73.1–87.0] | 52.2 (24/46) [38.1–65.9] |
| M1ensr_rs | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| M1ensra | 71.9 (41/57) [59.2–81.9] | 63.6 (77/121) [54.8–71.7] | 30.4 (14/46) [19.1–44.8] |
| M1ensx | 77.2 (44/57) [64.8–86.2] | 70.2 (85/121) [61.6–77.7] | 34.8 (16/46) [22.7–49.2] |
| M1ensx0 | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| M1ensxr | 77.2 (44/57) [64.8–86.2] | 70.2 (85/121) [61.6–77.7] | 34.8 (16/46) [22.7–49.2] |
| M1ensxr0 | 75.4 (43/57) [62.9–84.8] | 65.3 (79/121) [56.5–73.2] | 32.6 (15/46) [20.9–47.0] |
| M1fs | 71.9 (41/57) [59.2–81.9] | 59.5 (72/121) [50.6–67.8] | 23.9 (11/46) [13.9–37.9] |
| M1ip | 68.4 (39/57) [55.5–79.0] | 64.5 (78/121) [55.6–72.4] | 26.1 (12/46) [15.6–40.3] |
| M1lx | 68.4 (39/57) [55.5–79.0] | 63.6 (77/121) [54.8–71.7] | 26.1 (12/46) [15.6–40.3] |
| M1mr | 70.2 (40/57) [57.3–80.5] | 63.6 (77/121) [54.8–71.7] | 26.1 (12/46) [15.6–40.3] |
| M1r | 68.4 (39/57) [55.5–79.0] | 62.8 (76/121) [53.9–70.9] | 26.1 (12/46) [15.6–40.3] |
| M1rt | 70.2 (40/57) [57.3–80.5] | 62.8 (76/121) [53.9–70.9] | 28.3 (13/46) [17.3–42.5] |
| M1vf | 73.7 (42/57) [61.0–83.4] | 62.8 (76/121) [53.9–70.9] | 30.4 (14/46) [19.1–44.8] |

**Домэйн (хариулах, EX% (k/n) [Wilson 95% CI])**

| ID | applications | collection | cross | customers | deposits | gl_trends | loans | payments |
|---|---|---|---|---|---|---|---|---|
| M1a | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 25.0 (7/28) [12.7–43.4] | 75.0 (21/28) [56.6–87.3] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1ks | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 35.7 (10/28) [20.7–54.2] | 78.6 (22/28) [60.5–89.8] | 60.7 (17/28) [42.4–76.4] | 66.7 (18/27) [47.8–81.4] | 62.1 (18/29) [44.0–77.3] | 42.9 (12/28) [26.5–60.9] |
| M1rr | 71.4 (20/28) [52.9–84.7] | 50.0 (14/28) [32.6–67.4] | 21.4 (6/28) [10.2–39.5] | 78.6 (22/28) [60.5–89.8] | 71.4 (20/28) [52.9–84.7] | 59.3 (16/27) [40.7–75.5] | 75.9 (22/29) [57.9–87.8] | 39.3 (11/28) [23.6–57.6] |
| M1g8 | 71.4 (20/28) [52.9–84.7] | 57.1 (16/28) [39.1–73.5] | 25.0 (7/28) [12.7–43.4] | 67.9 (19/28) [49.3–82.1] | 64.3 (18/28) [45.8–79.3] | 70.4 (19/27) [51.5–84.1] | 51.7 (15/29) [34.4–68.6] | 42.9 (12/28) [26.5–60.9] |
| M1m | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 28.6 (8/28) [15.3–47.1] | 78.6 (22/28) [60.5–89.8] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1ens | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ensr | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ensp | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| A1 | 25.0 (7/28) [12.7–43.4] | 21.4 (6/28) [10.2–39.5] | 3.6 (1/28) [0.6–17.7] | 32.1 (9/28) [17.9–50.7] | 17.9 (5/28) [7.9–35.6] | 11.1 (3/27) [3.9–28.1] | 6.9 (2/29) [1.9–22.0] | 14.3 (4/28) [5.7–31.5] |
| A6 | 14.3 (4/28) [5.7–31.5] | 21.4 (6/28) [10.2–39.5] | 7.1 (2/28) [2.0–22.6] | 42.9 (12/28) [26.5–60.9] | 21.4 (6/28) [10.2–39.5] | 18.5 (5/27) [8.2–36.7] | 10.3 (3/29) [3.6–26.4] | 14.3 (4/28) [5.7–31.5] |
| A7 | 39.3 (11/28) [23.6–57.6] | 25.0 (7/28) [12.7–43.4] | 7.1 (2/28) [2.0–22.6] | 39.3 (11/28) [23.6–57.6] | 28.6 (8/28) [15.3–47.1] | 22.2 (6/27) [10.6–40.8] | 13.8 (4/29) [5.5–30.6] | 17.9 (5/28) [7.9–35.6] |
| C1 | 3.6 (1/28) [0.6–17.7] | 3.6 (1/28) [0.6–17.7] | 0.0 (0/28) [0.0–12.1] | 7.1 (2/28) [2.0–22.6] | 0.0 (0/28) [0.0–12.1] | 7.4 (2/27) [2.1–23.4] | 3.4 (1/29) [0.6–17.2] | 0.0 (0/28) [0.0–12.1] |
| C2 | 3.6 (1/28) [0.6–17.7] | 3.6 (1/28) [0.6–17.7] | 0.0 (0/28) [0.0–12.1] | 7.1 (2/28) [2.0–22.6] | 0.0 (0/28) [0.0–12.1] | 3.7 (1/27) [0.7–18.3] | 3.4 (1/29) [0.6–17.2] | 3.6 (1/28) [0.6–17.7] |
| C3 | 14.3 (4/28) [5.7–31.5] | 14.3 (4/28) [5.7–31.5] | 0.0 (0/28) [0.0–12.1] | 25.0 (7/28) [12.7–43.4] | 10.7 (3/28) [3.7–27.2] | 11.1 (3/27) [3.9–28.1] | 3.4 (1/29) [0.6–17.2] | 7.1 (2/28) [2.0–22.6] |
| E2 | 53.6 (15/28) [35.8–70.5] | 28.6 (8/28) [15.3–47.1] | 17.9 (5/28) [7.9–35.6] | 60.7 (17/28) [42.4–76.4] | 60.7 (17/28) [42.4–76.4] | 33.3 (9/27) [18.6–52.2] | 69.0 (20/29) [50.8–82.7] | 32.1 (9/28) [17.9–50.7] |
| I3 | 57.1 (16/28) [39.1–73.5] | 46.4 (13/28) [29.5–64.2] | 10.7 (3/28) [3.7–27.2] | 53.6 (15/28) [35.8–70.5] | 50.0 (14/28) [32.6–67.4] | 40.7 (11/27) [24.5–59.3] | 55.2 (16/29) [37.5–71.6] | 32.1 (9/28) [17.9–50.7] |
| I3r | 57.1 (16/28) [39.1–73.5] | 46.4 (13/28) [29.5–64.2] | 10.7 (3/28) [3.7–27.2] | 53.6 (15/28) [35.8–70.5] | 50.0 (14/28) [32.6–67.4] | 40.7 (11/27) [24.5–59.3] | 55.2 (16/29) [37.5–71.6] | 32.1 (9/28) [17.9–50.7] |
| I3r2 | 57.1 (16/28) [39.1–73.5] | 46.4 (13/28) [29.5–64.2] | 10.7 (3/28) [3.7–27.2] | 53.6 (15/28) [35.8–70.5] | 50.0 (14/28) [32.6–67.4] | 40.7 (11/27) [24.5–59.3] | 55.2 (16/29) [37.5–71.6] | 32.1 (9/28) [17.9–50.7] |
| I3t | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 14.3 (4/28) [5.7–31.5] | 57.1 (16/28) [39.1–73.5] | 50.0 (14/28) [32.6–67.4] | 51.9 (14/27) [34.0–69.3] | 58.6 (17/29) [40.7–74.5] | 32.1 (9/28) [17.9–50.7] |
| I3tr | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 14.3 (4/28) [5.7–31.5] | 57.1 (16/28) [39.1–73.5] | 50.0 (14/28) [32.6–67.4] | 51.9 (14/27) [34.0–69.3] | 58.6 (17/29) [40.7–74.5] | 32.1 (9/28) [17.9–50.7] |
| I3tr2 | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 14.3 (4/28) [5.7–31.5] | 57.1 (16/28) [39.1–73.5] | 50.0 (14/28) [32.6–67.4] | 51.9 (14/27) [34.0–69.3] | 58.6 (17/29) [40.7–74.5] | 32.1 (9/28) [17.9–50.7] |
| M1a_fx | 60.7 (17/28) [42.4–76.4] | 53.6 (15/28) [35.8–70.5] | 35.7 (10/28) [20.7–54.2] | 78.6 (22/28) [60.5–89.8] | 71.4 (20/28) [52.9–84.7] | 70.4 (19/27) [51.5–84.1] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1a_rs | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 25.0 (7/28) [12.7–43.4] | 75.0 (21/28) [56.6–87.3] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1a_tzn | 60.7 (17/28) [42.4–76.4] | 57.1 (16/28) [39.1–73.5] | 25.0 (7/28) [12.7–43.4] | 75.0 (21/28) [56.6–87.3] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 46.4 (13/28) [29.5–64.2] |
| M1afx | 60.7 (17/28) [42.4–76.4] | 53.6 (15/28) [35.8–70.5] | 32.1 (9/28) [17.9–50.7] | 82.1 (23/28) [64.4–92.1] | 71.4 (20/28) [52.9–84.7] | 70.4 (19/27) [51.5–84.1] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ensE2 | 78.6 (22/28) [60.5–89.8] | 57.1 (16/28) [39.1–73.5] | 42.9 (12/28) [26.5–60.9] | 82.1 (23/28) [64.4–92.1] | 67.9 (19/28) [49.3–82.1] | 74.1 (20/27) [55.3–86.8] | 86.2 (25/29) [69.4–94.5] | 50.0 (14/28) [32.6–67.4] |
| M1ensE2r | 78.6 (22/28) [60.5–89.8] | 57.1 (16/28) [39.1–73.5] | 42.9 (12/28) [26.5–60.9] | 82.1 (23/28) [64.4–92.1] | 67.9 (19/28) [49.3–82.1] | 74.1 (20/27) [55.3–86.8] | 86.2 (25/29) [69.4–94.5] | 50.0 (14/28) [32.6–67.4] |
| M1ensE2r_r7 | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 42.9 (12/28) [26.5–60.9] | 82.1 (23/28) [64.4–92.1] | 67.9 (19/28) [49.3–82.1] | 74.1 (20/27) [55.3–86.8] | 86.2 (25/29) [69.4–94.5] | 53.6 (15/28) [35.8–70.5] |
| M1ensE2rfx_r7 | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 42.9 (12/28) [26.5–60.9] | 82.1 (23/28) [64.4–92.1] | 75.0 (21/28) [56.6–87.3] | 74.1 (20/27) [55.3–86.8] | 86.2 (25/29) [69.4–94.5] | 57.1 (16/28) [39.1–73.5] |
| M1ens_r7 | 67.9 (19/28) [49.3–82.1] | 57.1 (16/28) [39.1–73.5] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 50.0 (14/28) [32.6–67.4] |
| M1ens_rs | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ens_tzn | 67.9 (19/28) [49.3–82.1] | 57.1 (16/28) [39.1–73.5] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 50.0 (14/28) [32.6–67.4] |
| M1ensfx | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 42.9 (12/28) [26.5–60.9] | 78.6 (22/28) [60.5–89.8] | 75.0 (21/28) [56.6–87.3] | 74.1 (20/27) [55.3–86.8] | 79.3 (23/29) [61.6–90.2] | 50.0 (14/28) [32.6–67.4] |
| M1ensfxl | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 42.9 (12/28) [26.5–60.9] | 78.6 (22/28) [60.5–89.8] | 82.1 (23/28) [64.4–92.1] | 74.1 (20/27) [55.3–86.8] | 86.2 (25/29) [69.4–94.5] | 53.6 (15/28) [35.8–70.5] |
| M1ensfxr | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 42.9 (12/28) [26.5–60.9] | 78.6 (22/28) [60.5–89.8] | 75.0 (21/28) [56.6–87.3] | 74.1 (20/27) [55.3–86.8] | 79.3 (23/29) [61.6–90.2] | 50.0 (14/28) [32.6–67.4] |
| M1ensfxv2 | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 42.9 (12/28) [26.5–60.9] | 78.6 (22/28) [60.5–89.8] | 75.0 (21/28) [56.6–87.3] | 74.1 (20/27) [55.3–86.8] | 79.3 (23/29) [61.6–90.2] | 50.0 (14/28) [32.6–67.4] |
| M1ensfxv2l | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 42.9 (12/28) [26.5–60.9] | 78.6 (22/28) [60.5–89.8] | 82.1 (23/28) [64.4–92.1] | 74.1 (20/27) [55.3–86.8] | 86.2 (25/29) [69.4–94.5] | 53.6 (15/28) [35.8–70.5] |
| M1ensfxv2lk | 75.0 (21/28) [56.6–87.3] | 67.9 (19/28) [49.3–82.1] | 57.1 (16/28) [39.1–73.5] | 85.7 (24/28) [68.5–94.3] | 82.1 (23/28) [64.4–92.1] | 74.1 (20/27) [55.3–86.8] | 93.1 (27/29) [78.0–98.1] | 71.4 (20/28) [52.9–84.7] |
| M1ensr_rs | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ensra | 64.3 (18/28) [45.8–79.3] | 42.9 (12/28) [26.5–60.9] | 28.6 (8/28) [15.3–47.1] | 78.6 (22/28) [60.5–89.8] | 60.7 (17/28) [42.4–76.4] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ensx | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 39.3 (11/28) [23.6–57.6] | 82.1 (23/28) [64.4–92.1] | 71.4 (20/28) [52.9–84.7] | 70.4 (19/27) [51.5–84.1] | 79.3 (23/29) [61.6–90.2] | 53.6 (15/28) [35.8–70.5] |
| M1ensx0 | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1ensxr | 67.9 (19/28) [49.3–82.1] | 53.6 (15/28) [35.8–70.5] | 39.3 (11/28) [23.6–57.6] | 82.1 (23/28) [64.4–92.1] | 71.4 (20/28) [52.9–84.7] | 70.4 (19/27) [51.5–84.1] | 79.3 (23/29) [61.6–90.2] | 53.6 (15/28) [35.8–70.5] |
| M1ensxr0 | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 32.1 (9/28) [17.9–50.7] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 74.1 (20/27) [55.3–86.8] | 75.9 (22/29) [57.9–87.8] | 46.4 (13/28) [29.5–64.2] |
| M1fs | 67.9 (19/28) [49.3–82.1] | 50.0 (14/28) [32.6–67.4] | 25.0 (7/28) [12.7–43.4] | 75.0 (21/28) [56.6–87.3] | 50.0 (14/28) [32.6–67.4] | 66.7 (18/27) [47.8–81.4] | 65.5 (19/29) [47.3–80.1] | 42.9 (12/28) [26.5–60.9] |
| M1ip | 60.7 (17/28) [42.4–76.4] | 46.4 (13/28) [29.5–64.2] | 28.6 (8/28) [15.3–47.1] | 67.9 (19/28) [49.3–82.1] | 60.7 (17/28) [42.4–76.4] | 70.4 (19/27) [51.5–84.1] | 75.9 (22/29) [57.9–87.8] | 50.0 (14/28) [32.6–67.4] |
| M1lx | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 25.0 (7/28) [12.7–43.4] | 78.6 (22/28) [60.5–89.8] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1mr | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 28.6 (8/28) [15.3–47.1] | 78.6 (22/28) [60.5–89.8] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1r | 60.7 (17/28) [42.4–76.4] | 50.0 (14/28) [32.6–67.4] | 25.0 (7/28) [12.7–43.4] | 75.0 (21/28) [56.6–87.3] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1rt | 64.3 (18/28) [45.8–79.3] | 50.0 (14/28) [32.6–67.4] | 25.0 (7/28) [12.7–43.4] | 78.6 (22/28) [60.5–89.8] | 57.1 (16/28) [39.1–73.5] | 70.4 (19/27) [51.5–84.1] | 72.4 (21/29) [54.3–85.3] | 42.9 (12/28) [26.5–60.9] |
| M1vf | 67.9 (19/28) [49.3–82.1] | 57.1 (16/28) [39.1–73.5] | 17.9 (5/28) [7.9–35.6] | 78.6 (22/28) [60.5–89.8] | 64.3 (18/28) [45.8–79.3] | 70.4 (19/27) [51.5–84.1] | 65.5 (19/29) [47.3–80.1] | 50.0 (14/28) [32.6–67.4] |

## McNemar (exact)

| Split | A → B | Хэмжүүр | A зөв | B зөв | +b / −c | p | Тэмдэглэл |
|---|---|---|---|---|---|---|---|
| test | M1a → M1r | нийт | 101/200 | 113/200 | +15 / −3 | 0.00754 |  |
| test | M1a → M1r | хариулах | 81/151 | 78/151 | +0 / −3 | 0.25 |  |
| test | M1a → M1r | боломжгүй (тоо) | 6/24 | 16/24 | +10 / −0 | — | |
| test | M1a → M1r | хоёрдмол (тоо) | 14/25 | 19/25 | +5 / −0 | — | |
| test | M1a → M1ens | нийт | 101/200 | 110/200 | +11 / −2 | 0.0225 |  |
| test | M1a → M1ens | хариулах | 81/151 | 90/151 | +10 / −1 | 0.0117 |  |
| test | M1a → M1ens | боломжгүй (тоо) | 6/24 | 6/24 | +1 / −1 | — | |
| test | M1a → M1ens | хоёрдмол (тоо) | 14/25 | 14/25 | +0 / −0 | — | |
| test | M1a → M1ensr | нийт | 101/200 | 120/200 | +23 / −4 | 3.11e-04 |  |
| test | M1a → M1ensr | хариулах | 81/151 | 87/151 | +10 / −4 | 0.18 |  |
| test | M1a → M1ensr | боломжгүй (тоо) | 6/24 | 14/24 | +8 / −0 | — | |
| test | M1a → M1ensr | хоёрдмол (тоо) | 14/25 | 19/25 | +5 / −0 | — | |
| test | I3t → M1ensr | нийт | 77/200 | 120/200 | +55 / −12 | 1.03e-07 |  |
| test | I3t → M1ensr | хариулах | 62/151 | 87/151 | +37 / −12 | 4.70e-04 |  |
| test | I3t → M1ensr | боломжгүй (тоо) | 5/24 | 14/24 | +9 / −0 | — | |
| test | I3t → M1ensr | хоёрдмол (тоо) | 10/25 | 19/25 | +9 / −0 | — | |
| test | M1ens → M1ensr | нийт | 110/200 | 120/200 | +13 / −3 | 0.0213 |  |
| test | M1ens → M1ensr | хариулах | 90/151 | 87/151 | +0 / −3 | 0.25 |  |
| test | M1ens → M1ensr | боломжгүй (тоо) | 6/24 | 14/24 | +8 / −0 | — | |
| test | M1ens → M1ensr | хоёрдмол (тоо) | 14/25 | 19/25 | +5 / −0 | — | |
| test | M1ensfx → M1ensfxv2 | нийт | 116/200 | 143/200 | +27 / −0 | 1.49e-08 |  |
| test | M1ensfx → M1ensfxv2 | хариулах | 95/151 | 95/151 | +0 / −0 | 1 |  |
| test | M1ensfx → M1ensfxv2 | боломжгүй (тоо) | 6/24 | 24/24 | +18 / −0 | — | |
| test | M1ensfx → M1ensfxv2 | хоёрдмол (тоо) | 15/25 | 24/25 | +9 / −0 | — | |
| test | M1ensfx → M1ensfxl | нийт | 116/200 | 120/200 | +4 / −0 | 0.125 |  |
| test | M1ensfx → M1ensfxl | хариулах | 95/151 | 99/151 | +4 / −0 | 0.125 |  |
| test | M1ensfx → M1ensfxl | боломжгүй (тоо) | 6/24 | 6/24 | +0 / −0 | — | |
| test | M1ensfx → M1ensfxl | хоёрдмол (тоо) | 15/25 | 15/25 | +0 / −0 | — | |
| test | M1ensfx → M1ensfxv2l | нийт | 116/200 | 147/200 | +31 / −0 | 9.31e-10 |  |
| test | M1ensfx → M1ensfxv2l | хариулах | 95/151 | 99/151 | +4 / −0 | 0.125 |  |
| test | M1ensfx → M1ensfxv2l | боломжгүй (тоо) | 6/24 | 24/24 | +18 / −0 | — | |
| test | M1ensfx → M1ensfxv2l | хоёрдмол (тоо) | 15/25 | 24/25 | +9 / −0 | — | |
| test | M1ensfxv2l → M1ensfxv2lk | нийт | 147/200 | 156/200 | +10 / −1 | 0.0117 |  |
| test | M1ensfxv2l → M1ensfxv2lk | хариулах | 99/151 | 108/151 | +10 / −1 | 0.0117 |  |
| test | M1ensfxv2l → M1ensfxv2lk | боломжгүй (тоо) | 24/24 | 24/24 | +0 / −0 | — | |
| test | M1ensfxv2l → M1ensfxv2lk | хоёрдмол (тоо) | 24/25 | 24/25 | +0 / −0 | — | |
| test | M1ens → M1ensfxv2lk | нийт | 110/200 | 156/200 | +46 / −0 | 2.84e-14 |  |
| test | M1ens → M1ensfxv2lk | хариулах | 90/151 | 108/151 | +18 / −0 | 7.63e-06 |  |
| test | M1ens → M1ensfxv2lk | боломжгүй (тоо) | 6/24 | 24/24 | +18 / −0 | — | |
| test | M1ens → M1ensfxv2lk | хоёрдмол (тоо) | 14/25 | 24/25 | +10 / −0 | — | |
| dev | M1ens → M1ensp | нийт | 176/300 | 176/300 | +0 / −0 | 1 |  |
| dev | M1ens → M1ensp | хариулах | 137/224 | 137/224 | +0 / −0 | 1 |  |
| dev | M1ens → M1ensp | боломжгүй (тоо) | 18/38 | 18/38 | +0 / −0 | — | |
| dev | M1ens → M1ensp | хоёрдмол (тоо) | 21/38 | 21/38 | +0 / −0 | — | |
| dev | M1ensr → M1enspr | — | — | — | run алга: M1enspr | — | |
| dev | M1ensfx → M1ensfxv2 | нийт | 185/300 | 217/300 | +32 / −0 | 4.66e-10 |  |
| dev | M1ensfx → M1ensfxv2 | хариулах | 146/224 | 146/224 | +0 / −0 | 1 |  |
| dev | M1ensfx → M1ensfxv2 | боломжгүй (тоо) | 18/38 | 38/38 | +20 / −0 | — | |
| dev | M1ensfx → M1ensfxv2 | хоёрдмол (тоо) | 21/38 | 33/38 | +12 / −0 | — | |
| dev | M1ensfx → M1ensfxl | нийт | 185/300 | 190/300 | +5 / −0 | 0.0625 |  |
| dev | M1ensfx → M1ensfxl | хариулах | 146/224 | 151/224 | +5 / −0 | 0.0625 |  |
| dev | M1ensfx → M1ensfxl | боломжгүй (тоо) | 18/38 | 18/38 | +0 / −0 | — | |
| dev | M1ensfx → M1ensfxl | хоёрдмол (тоо) | 21/38 | 21/38 | +0 / −0 | — | |
| dev | M1ensfx → M1ensfxv2l | нийт | 185/300 | 222/300 | +37 / −0 | 1.46e-11 |  |
| dev | M1ensfx → M1ensfxv2l | хариулах | 146/224 | 151/224 | +5 / −0 | 0.0625 |  |
| dev | M1ensfx → M1ensfxv2l | боломжгүй (тоо) | 18/38 | 38/38 | +20 / −0 | — | |
| dev | M1ensfx → M1ensfxv2l | хоёрдмол (тоо) | 21/38 | 33/38 | +12 / −0 | — | |
| dev | M1ensfxv2l → M1ensfxv2lk | нийт | 222/300 | 241/300 | +19 / −0 | 3.81e-06 |  |
| dev | M1ensfxv2l → M1ensfxv2lk | хариулах | 151/224 | 170/224 | +19 / −0 | 3.81e-06 |  |
| dev | M1ensfxv2l → M1ensfxv2lk | боломжгүй (тоо) | 38/38 | 38/38 | +0 / −0 | — | |
| dev | M1ensfxv2l → M1ensfxv2lk | хоёрдмол (тоо) | 33/38 | 33/38 | +0 / −0 | — | |
| dev | M1ens → M1ensfxv2lk | нийт | 176/300 | 241/300 | +65 / −0 | 5.42e-20 |  |
| dev | M1ens → M1ensfxv2lk | хариулах | 137/224 | 170/224 | +33 / −0 | 2.33e-10 |  |
| dev | M1ens → M1ensfxv2lk | боломжгүй (тоо) | 18/38 | 38/38 | +20 / −0 | — | |
| dev | M1ens → M1ensfxv2lk | хоёрдмол (тоо) | 21/38 | 33/38 | +12 / −0 | — | |

b = A буруу, B зөв (B ахисан); c = A зөв, B буруу. Урьдчилан зарласан тест нь «нийт» ба «хариулах»; ангиллын мөрүүд зөвхөн тоо (p-гүй).

## Шийдвэр (docs/ACCURACY_PLAN.md)

**PICK_TEST=0** — dev хариулах: M1ensp 137 vs M1ens 137 /224, +0/−0, p = 1 → батлагдаагүй, PICK_TEST=0 (M1ens/M1ensr хэвээр).

Дараагийн сессийн env (config/kaggle_overrides*.env-д гараар нэмнэ):

```sh
# V26-ийн dev дүнгээс (scripts/ingest/ingest_results.py, 2026-11-02)
# dev хариулах: M1ensp 137 vs M1ens 137 /224, +0/−0, p = 1 → батлагдаагүй, PICK_TEST=0 (M1ens/M1ensr хэвээр).
PICK_TEST=0
```

## Ch6-д (LaTeX, гараар хуулна)

```latex
% --- Ch6: test-ийн үр дүн (V26); CI = Wilson score 95% интервал ---
\begin{tabular}{lrrrrrr}
\toprule
ID & Нийт /200 & Wilson 95\% CI & Хариулах /151 & Wilson 95\% CI & Боломжгүй /24 & Хоёрдмол /25 \\
\midrule
Үргэлж тодруулах (суурь) & 49/200 (24.5\%) & 19.1--30.9 & 0/151 (0.0\%) & 0.0--2.5 & 24 & 25 \\
\midrule
I3t & 77/200 (38.5\%) & 32.0--45.4 & 62/151 (41.1\%) & 33.5--49.0 & 5 & 10 \\
M1a & 101/200 (50.5\%) & 43.6--57.4 & 81/151 (53.6\%) & 45.7--61.4 & 6 & 14 \\
M1ks & 100/200 (50.0\%) & 43.1--56.9 & 82/151 (54.3\%) & 46.4--62.0 & 6 & 12 \\
M1rr & 100/200 (50.0\%) & 43.1--56.9 & 81/151 (53.6\%) & 45.7--61.4 & 8 & 11 \\
M1g8 & 96/200 (48.0\%) & 41.2--54.9 & 74/151 (49.0\%) & 41.2--56.9 & 8 & 14 \\
M1m & 102/200 (51.0\%) & 44.1--57.8 & 82/151 (54.3\%) & 46.4--62.0 & 6 & 14 \\
M1r & 113/200 (56.5\%) & 49.6--63.2 & 78/151 (51.7\%) & 43.7--59.5 & 16 & 19 \\
M1ens & 110/200 (55.0\%) & 48.1--61.7 & 90/151 (59.6\%) & 51.6--67.1 & 6 & 14 \\
M1ensr & 120/200 (60.0\%) & 53.1--66.5 & 87/151 (57.6\%) & 49.6--65.2 & 14 & 19 \\
E2 & 77/200 (38.5\%) & 32.0--45.4 & 62/151 (41.1\%) & 33.5--49.0 & 5 & 10 \\
E3c & 93/200 (46.5\%) & 39.7--53.4 & 59/151 (39.1\%) & 31.7--47.0 & 17 & 17 \\
E4 & 90/200 (45.0\%) & 38.3--51.9 & 64/151 (42.4\%) & 34.8--50.4 & 10 & 16 \\
E4f & 81/200 (40.5\%) & 33.9--47.4 & 65/151 (43.0\%) & 35.4--51.0 & 5 & 11 \\
I3 & 75/200 (37.5\%) & 31.1--44.4 & 60/151 (39.7\%) & 32.3--47.7 & 5 & 10 \\
I3r & 88/200 (44.0\%) & 37.3--50.9 & 58/151 (38.4\%) & 31.0--46.4 & 15 & 15 \\
I3r2 & 88/200 (44.0\%) & 37.3--50.9 & 58/151 (38.4\%) & 31.0--46.4 & 15 & 15 \\
I3tr & 90/200 (45.0\%) & 38.3--51.9 & 60/151 (39.7\%) & 32.3--47.7 & 15 & 15 \\
I3tr2 & 90/200 (45.0\%) & 38.3--51.9 & 60/151 (39.7\%) & 32.3--47.7 & 15 & 15 \\
M1a\_fx & 108/200 (54.0\%) & 47.1--60.8 & 87/151 (57.6\%) & 49.6--65.2 & 6 & 15 \\
M1a\_rs & 101/200 (50.5\%) & 43.6--57.4 & 81/151 (53.6\%) & 45.7--61.4 & 6 & 14 \\
M1a\_tzn & 101/200 (50.5\%) & 43.6--57.4 & 81/151 (53.6\%) & 45.7--61.4 & 6 & 14 \\
M1ensE2 & 109/200 (54.5\%) & 47.6--61.3 & 90/151 (59.6\%) & 51.6--67.1 & 5 & 14 \\
M1ensE2r & 121/200 (60.5\%) & 53.6--67.0 & 88/151 (58.3\%) & 50.3--65.8 & 14 & 19 \\
M1ensE2r\_r7 & 121/200 (60.5\%) & 53.6--67.0 & 88/151 (58.3\%) & 50.3--65.8 & 14 & 19 \\
M1ensE2rfx\_r7 & 123/200 (61.5\%) & 54.6--68.0 & 90/151 (59.6\%) & 51.6--67.1 & 13 & 20 \\
M1ens\_r7 & 110/200 (55.0\%) & 48.1--61.7 & 90/151 (59.6\%) & 51.6--67.1 & 6 & 14 \\
M1ens\_rs & 110/200 (55.0\%) & 48.1--61.7 & 90/151 (59.6\%) & 51.6--67.1 & 6 & 14 \\
M1ens\_tzn & 110/200 (55.0\%) & 48.1--61.7 & 90/151 (59.6\%) & 51.6--67.1 & 6 & 14 \\
M1ensfx & 116/200 (58.0\%) & 51.1--64.6 & 95/151 (62.9\%) & 55.0--70.2 & 6 & 15 \\
M1ensfxl & 120/200 (60.0\%) & 53.1--66.5 & 99/151 (65.6\%) & 57.7--72.7 & 6 & 15 \\
M1ensfxr & 125/200 (62.5\%) & 55.6--68.9 & 92/151 (60.9\%) & 53.0--68.3 & 13 & 20 \\
M1ensfxv2 & 143/200 (71.5\%) & 64.9--77.3 & 95/151 (62.9\%) & 55.0--70.2 & 24 & 24 \\
M1ensfxv2l & 147/200 (73.5\%) & 67.0--79.1 & 99/151 (65.6\%) & 57.7--72.7 & 24 & 24 \\
M1ensfxv2lk & 156/200 (78.0\%) & 71.8--83.2 & 108/151 (71.5\%) & 63.9--78.1 & 24 & 24 \\
M1ensr\_rs & 120/200 (60.0\%) & 53.1--66.5 & 87/151 (57.6\%) & 49.6--65.2 & 14 & 19 \\
\bottomrule
\end{tabular}

% --- tab:mcnemar-д нэмэх мөрүүд (exact McNemar; нийт мөр бүрийн хажууд хариулах ба ангиллын тоо) ---
test M1a → M1r & нийт & +15 / −3 & 0.00754 \\
test M1a → M1r & хариулах боломжтой & +0 / −3 & 0.25 \\
test M1a → M1r & боломжгүй (тоо) & +10 / −0 & --- \\
test M1a → M1r & хоёрдмол (тоо) & +5 / −0 & --- \\
test M1a → M1ens & нийт & +11 / −2 & 0.0225 \\
test M1a → M1ens & хариулах боломжтой & +10 / −1 & 0.0117 \\
test M1a → M1ens & боломжгүй (тоо) & +1 / −1 & --- \\
test M1a → M1ens & хоёрдмол (тоо) & +0 / −0 & --- \\
test M1a → M1ensr & нийт & +23 / −4 & $3.11 \times 10^{-4}$ \\
test M1a → M1ensr & хариулах боломжтой & +10 / −4 & 0.18 \\
test M1a → M1ensr & боломжгүй (тоо) & +8 / −0 & --- \\
test M1a → M1ensr & хоёрдмол (тоо) & +5 / −0 & --- \\
test I3t → M1ensr & нийт & +55 / −12 & $1.03 \times 10^{-7}$ \\
test I3t → M1ensr & хариулах боломжтой & +37 / −12 & $4.70 \times 10^{-4}$ \\
test I3t → M1ensr & боломжгүй (тоо) & +9 / −0 & --- \\
test I3t → M1ensr & хоёрдмол (тоо) & +9 / −0 & --- \\
test M1ens → M1ensr & нийт & +13 / −3 & 0.0213 \\
test M1ens → M1ensr & хариулах боломжтой & +0 / −3 & 0.25 \\
test M1ens → M1ensr & боломжгүй (тоо) & +8 / −0 & --- \\
test M1ens → M1ensr & хоёрдмол (тоо) & +5 / −0 & --- \\
test M1ensfx → M1ensfxv2 & нийт & +27 / −0 & $1.49 \times 10^{-8}$ \\
test M1ensfx → M1ensfxv2 & хариулах боломжтой & +0 / −0 & 1 \\
test M1ensfx → M1ensfxv2 & боломжгүй (тоо) & +18 / −0 & --- \\
test M1ensfx → M1ensfxv2 & хоёрдмол (тоо) & +9 / −0 & --- \\
test M1ensfx → M1ensfxl & нийт & +4 / −0 & 0.125 \\
test M1ensfx → M1ensfxl & хариулах боломжтой & +4 / −0 & 0.125 \\
test M1ensfx → M1ensfxl & боломжгүй (тоо) & +0 / −0 & --- \\
test M1ensfx → M1ensfxl & хоёрдмол (тоо) & +0 / −0 & --- \\
test M1ensfx → M1ensfxv2l & нийт & +31 / −0 & $9.31 \times 10^{-10}$ \\
test M1ensfx → M1ensfxv2l & хариулах боломжтой & +4 / −0 & 0.125 \\
test M1ensfx → M1ensfxv2l & боломжгүй (тоо) & +18 / −0 & --- \\
test M1ensfx → M1ensfxv2l & хоёрдмол (тоо) & +9 / −0 & --- \\
test M1ensfxv2l → M1ensfxv2lk & нийт & +10 / −1 & 0.0117 \\
test M1ensfxv2l → M1ensfxv2lk & хариулах боломжтой & +10 / −1 & 0.0117 \\
test M1ensfxv2l → M1ensfxv2lk & боломжгүй (тоо) & +0 / −0 & --- \\
test M1ensfxv2l → M1ensfxv2lk & хоёрдмол (тоо) & +0 / −0 & --- \\
test M1ens → M1ensfxv2lk & нийт & +46 / −0 & $2.84 \times 10^{-14}$ \\
test M1ens → M1ensfxv2lk & хариулах боломжтой & +18 / −0 & $7.63 \times 10^{-6}$ \\
test M1ens → M1ensfxv2lk & боломжгүй (тоо) & +18 / −0 & --- \\
test M1ens → M1ensfxv2lk & хоёрдмол (тоо) & +10 / −0 & --- \\
```

## Слайд 11/16

```text
Слайд 11 (test-ийн үр дүн; [ ] = Wilson 95% CI):
  - Headline (урьдчилан зарласан, --headline M1ens): M1ens нийт 110/200 (55.0%; Wilson 95% CI 48.1–61.7), хариулах 90/151 (59.6%; Wilson 95% CI 51.6–67.1)
  - M1a: нийт 101/200 (50.5%) [43.6–57.4], хариулах 81/151 (53.6%) [45.7–61.4]
  - M1r: нийт 113/200 (56.5%) [49.6–63.2], хариулах 78/151 (51.7%) [43.7–59.5]
  - M1ens: нийт 110/200 (55.0%) [48.1–61.7], хариулах 90/151 (59.6%) [51.6–67.1]
  - M1ensr: нийт 120/200 (60.0%) [53.1–66.5], хариулах 87/151 (57.6%) [49.6–65.2]
  - Үргэлж тодруулах (суурь): нийт 49/200 (24.5%; Wilson 95% CI 19.1–30.9) — хариулах боломжгүй ба хоёрдмол бүгд «зөв»
Слайд 16 (статистик ба дүгнэлт; exact McNemar):
  - M1a → M1r: нийт +15/−3, p = 0.00754 (ач холбогдолтой); хариулах +0/−3, p = 0.25 (ач холбогдолгүй) [боломжгүй +10/−0, хоёрдмол +5/−0]
  - M1a → M1ens: нийт +11/−2, p = 0.0225 (ач холбогдолтой); хариулах +10/−1, p = 0.0117 (ач холбогдолтой) [боломжгүй +1/−1, хоёрдмол +0/−0]
  - M1a → M1ensr: нийт +23/−4, p = 3.11e-04 (ач холбогдолтой); хариулах +10/−4, p = 0.18 (ач холбогдолгүй) [боломжгүй +8/−0, хоёрдмол +5/−0]
  - I3t → M1ensr: нийт +55/−12, p = 1.03e-07 (ач холбогдолтой); хариулах +37/−12, p = 4.70e-04 (ач холбогдолтой) [боломжгүй +9/−0, хоёрдмол +9/−0]
  - M1ens → M1ensr: нийт +13/−3, p = 0.0213 (ач холбогдолтой); хариулах +0/−3, p = 0.25 (ач холбогдолгүй) [боломжгүй +8/−0, хоёрдмол +5/−0]
  - M1ensfx → M1ensfxv2: нийт +27/−0, p = 1.49e-08 (ач холбогдолтой); хариулах +0/−0, p = 1 (ач холбогдолгүй) [боломжгүй +18/−0, хоёрдмол +9/−0]
  - M1ensfx → M1ensfxl: нийт +4/−0, p = 0.125 (ач холбогдолгүй); хариулах +4/−0, p = 0.125 (ач холбогдолгүй) [боломжгүй +0/−0, хоёрдмол +0/−0]
  - M1ensfx → M1ensfxv2l: нийт +31/−0, p = 9.31e-10 (ач холбогдолтой); хариулах +4/−0, p = 0.125 (ач холбогдолгүй) [боломжгүй +18/−0, хоёрдмол +9/−0]
  - M1ensfxv2l → M1ensfxv2lk: нийт +10/−1, p = 0.0117 (ач холбогдолтой); хариулах +10/−1, p = 0.0117 (ач холбогдолтой) [боломжгүй +0/−0, хоёрдмол +0/−0]
  - M1ens → M1ensfxv2lk: нийт +46/−0, p = 2.84e-14 (ач холбогдолтой); хариулах +18/−0, p = 7.63e-06 (ач холбогдолтой) [боломжгүй +18/−0, хоёрдмол +10/−0]
  - Хос харьцуулалт (dev): dev хариулах: M1ensp 137 vs M1ens 137 /224, +0/−0, p = 1 → батлагдаагүй, PICK_TEST=0 (M1ens/M1ensr хэвээр).
```
