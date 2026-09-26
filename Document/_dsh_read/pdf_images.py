"""Extract the embedded page images from a scanned PDF.

A scanned PDF carries no text layer, so the only way to read it is to pull the
page images out and look at them. Uses the same local pypdf as pdf_text.py.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "libs"))

from pypdf import PdfReader  # noqa: E402

src = pathlib.Path(sys.argv[1])
outdir = pathlib.Path(sys.argv[2])
outdir.mkdir(parents=True, exist_ok=True)

reader = PdfReader(str(src))
total = 0
for i, page in enumerate(reader.pages, 1):
    try:
        images = list(page.images)
    except Exception as e:
        print("page %d: <error %s: %s>" % (i, type(e).__name__, e))
        continue
    for j, img in enumerate(images, 1):
        data = img.data
        name = img.name or ("page%d_%d.png" % (i, j))
        suffix = pathlib.Path(name).suffix.lower() or ".png"
        dst = outdir / ("p%02d_%d%s" % (i, j, suffix))
        dst.write_bytes(data)
        total += 1
        print("page %-3d image %-2d -> %-16s %9d bytes  (source %s)" % (i, j, dst.name, len(data), name))
print("images written:", total)
