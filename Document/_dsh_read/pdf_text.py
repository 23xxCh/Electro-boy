"""Extract text from a PDF using the locally-unzipped pypdf (no install needed)."""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "libs"))

from pypdf import PdfReader  # noqa: E402

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])

reader = PdfReader(str(src))
n = len(reader.pages)
print("file      :", src.name)
print("pages     :", n)
print("encrypted :", reader.is_encrypted)
try:
    meta = reader.metadata or {}
    print("title     :", (meta.get("/Title") or "")[:120])
except Exception as e:  # pragma: no cover
    print("title     : <err %s>" % e)

chunks = []
empty = 0
for i, page in enumerate(reader.pages):
    try:
        t = page.extract_text() or ""
    except Exception as e:
        t = "<extraction error: %s: %s>" % (type(e).__name__, e)
    if not t.strip():
        empty += 1
    chunks.append("\n\n========== [page %d] ==========\n%s" % (i + 1, t))

text = "".join(chunks)
dst.write_text(text, encoding="utf-8")

han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
print("empty pages:", empty)
print("chars     :", len(text), " han chars:", han)
print("written   :", dst)
