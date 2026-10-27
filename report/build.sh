#!/bin/bash
# LaTeX build script — mirrors VS Code LaTeX Workshop recipe:
#   pdflatex -> biber -> pdflatex x2
# pdflatex нь MiKTeX-ийн шинэчлэлийн мэдэгдэл эсвэл longtable-ийн "ignored error"-оос болж exit 1 буцааж болно.
# Иймд pdflatex-ийн exit кодоор зогсохгүй (biber дараагийн алхамд заавал ажиллана); эцэст нь PDF ба фатал алдааг шалгана.

set -e

DOC="main"
OUTDIR="out"

mkdir -p "$OUTDIR"/{FrontBackMatter,Chapters,Appendices}

run_pdflatex() {
  pdflatex -synctex=1 -interaction=nonstopmode -file-line-error -output-directory="$OUTDIR" "$DOC" \
    || echo "(pdflatex exit $? — үргэлжлүүлнэ; фатал алдааг эцэст нь шалгана)"
}

echo "=== [1/4] pdflatex ==="
run_pdflatex

echo "=== [2/4] biber ==="
biber "$OUTDIR/$DOC"

echo "=== [3/4] pdflatex ==="
run_pdflatex

echo "=== [4/4] pdflatex ==="
run_pdflatex

if grep -a -q "^! " "$OUTDIR/$DOC.log" || [ ! -s "$OUTDIR/$DOC.pdf" ]; then
  echo "=== АЛДАА: фатал LaTeX алдаа эсвэл PDF үүсээгүй ($OUTDIR/$DOC.log) ==="
  exit 1
fi
echo "=== Done: $OUTDIR/$DOC.pdf ==="
