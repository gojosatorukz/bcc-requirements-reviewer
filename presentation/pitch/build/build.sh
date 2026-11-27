#!/bin/sh
# Пересобрать питч: index.html → PDF (Chrome) → PNG → PPTX с заметками.
# Один раз: npm install && python3 -m venv .venv && .venv/bin/pip install pymupdf
set -e
# Внимание: каждая пересборка меняет байты PDF и PPTX (дата создания). После неё обновите runs/bcc-jury/materials.json и ZIP.
cd "$(dirname "$0")"
OUT=..
node deck.js "$OUT"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$OUT/razbor-tz-pitch.pdf" "file://$(cd "$OUT" && pwd)/index.html" 2>/dev/null
.venv/bin/python -c "
import pymupdf,sys
for i,p in enumerate(pymupdf.open(sys.argv[1])): p.get_pixmap(dpi=144).save(f'hi-{i+1:02d}.png')
" "$OUT/razbor-tz-pitch.pdf"
node pptx.js "$OUT/razbor-tz-pitch.pptx"
shasum -a 256 "$OUT/razbor-tz-pitch.pdf" "$OUT/razbor-tz-pitch.pptx"
