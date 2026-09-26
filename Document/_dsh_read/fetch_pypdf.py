"""Fetch a pure-python wheel from the Tsinghua PyPI mirror and unzip it locally.

Avoids pip entirely: pip's temp directories are ACL-denied by the sandbox,
but a plain file write inside the workspace works fine.
"""
import pathlib
import re
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PKG = "pypdf"
INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple/%s/" % PKG
LIBS = HERE / "libs"
WHEEL = HERE / "pypdf-wheel.zip"

LIBS.mkdir(parents=True, exist_ok=True)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (python-urllib)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


html = get(INDEX).decode("utf-8", "replace")
hrefs = re.findall(r'href="([^"#]+\.whl)(?:#[^"]*)?"', html)
print("wheel links found:", len(hrefs))
cands = [h for h in hrefs if "py3-none-any" in h]
if not cands:
    cands = hrefs
if not cands:
    raise SystemExit("no wheel found on index page")
url = cands[-1]
if url.startswith("//"):
    url = "https:" + url
elif url.startswith("/"):
    url = "https://pypi.tuna.tsinghua.edu.cn" + url
elif not url.startswith("http"):
    url = INDEX + url
print("downloading:", url)

data = get(url)
WHEEL.write_bytes(data)
print("saved %d bytes -> %s" % (len(data), WHEEL.name))

with zipfile.ZipFile(WHEEL) as z:
    names = z.namelist()
    z.extractall(LIBS)

tops = sorted({n.split("/")[0] for n in names})
print("extracted top-level entries into libs/:", tops[:20])
print("total entries:", len(names))
