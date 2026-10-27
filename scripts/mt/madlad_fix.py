"""MADLAD-400 (T5) embedding-ийн холболтыг transformers 5 дээр засах.

Kaggle-ийн transformers 5.x нь MADLAD-ийг ачаалахдаа «shared.weight ба decoder.embed_tokens.weight өөр утгатай тул tie хийхгүй»
гэж анхааруулаад загвар хог («ununun…») гаргасан (WORKLOG §6.21, Version 13–14). Аль embedding зөв болохыг урьдчилан мэдэхгүй
тул encoder/decoder-ийн embedding-ийг (ачаалсан хэвээр / shared / encoder-ийнх / decoder-ийнх) хувилбараар холбож, туршилтын
өгүүлбэрийн орчуулгаар хамгийн «англи» гаралттайг сонгоно. Жинг хуулахгүй, зөвхөн module-ийн заагчийг солино.
"""

from __future__ import annotations

import re

PROBES = ["<2en> 2025 онд нийт хэдэн зээлийн хүсэлт ирсэн бэ?",
          "<2en> Салбар бүрийн идэвхтэй зээлийн тоог гарга.",
          "<2en> Сайн байна уу?"]


def _quality(text: str) -> float:
    """0..1: латин үсэг/зайн хувь × давтагдаагүй үгийн хувь (хог: «ununun», «হহহ» → ~0)."""
    if not text.strip():
        return 0.0
    ascii_ratio = len(re.findall(r"[A-Za-z ,.?'0-9-]", text)) / len(text)
    words = text.split()
    uniq = len(set(words)) / max(1, len(words))
    longest = max((len(w) for w in words), default=0)
    return ascii_ratio * uniq * (0.2 if longest > 25 else 1.0)


def fix_embeddings(model, tok, verbose: bool = True) -> str:
    import torch
    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    enc_e, dec_e, shared = base.encoder.embed_tokens, base.decoder.embed_tokens, base.shared
    variants = {"loaded": (enc_e, dec_e), "shared": (shared, shared), "encoder": (enc_e, enc_e), "decoder": (dec_e, dec_e)}
    device = next(base.parameters()).device
    x = tok(PROBES, return_tensors="pt", padding=True).to(device)
    scores = {}
    for name, (e, d) in variants.items():
        base.encoder.embed_tokens, base.decoder.embed_tokens = e, d
        with torch.no_grad():
            g = model.generate(**x, max_new_tokens=24, num_beams=1, do_sample=False)
        outs = tok.batch_decode(g, skip_special_tokens=True)
        scores[name] = sum(_quality(o) for o in outs) / len(outs)
        if verbose:
            print(f"  [embedding:{name}] {scores[name]:.2f} {outs[0][:80]!r}", flush=True)
    best = max(scores, key=lambda k: (scores[k], k == "loaded"))
    base.encoder.embed_tokens, base.decoder.embed_tokens = variants[best]
    if variants[best][0] is variants[best][1]:
        # shared-ийг ч мөн тэр embedding руу заана: ашиглагдахгүй 256k×d хуулбар (fp32-д ~2 GB) чөлөөлөгдөнө
        # (Version 17: adapter-тай орчуулга batch 1-д ч T4-д OOM — 14 GB-ийн ихэнх нь тусдаа 3 embedding)
        base.shared = variants[best][0]
        del variants, enc_e, dec_e, shared
        import gc
        gc.collect()
        torch.cuda.empty_cache()
    if verbose:
        print(f"  [embedding] сонгосон: {best} ({scores[best]:.2f})", flush=True)
    if scores[best] < 0.3:
        print("  [embedding] АНХААР: аль ч хувилбар англи гаралт өгсөнгүй — загвар/хувилбарын асуудал", flush=True)
    return best
