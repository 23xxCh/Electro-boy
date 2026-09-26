import pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "libs"))
from pypdf import PdfReader

p = pathlib.Path(sys.argv[1])
r = PdfReader(str(p))
m = r.metadata
for k in sorted(m.keys() if hasattr(m, "keys") else []):
    try:
        print("%-20s %s" % (k, m.get(k)))
    except Exception as e:
        print(k, "err", e)
print("pages:", len(r.pages))
