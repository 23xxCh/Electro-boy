"""Extract text from a .docx using only the standard library (docx is a zip of XML)."""
import html
import pathlib
import re
import sys
import zipfile

path = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])

with zipfile.ZipFile(path) as z:
    names = z.namelist()
    xml = z.read("word/document.xml").decode("utf-8", "replace")
    media = [n for n in names if n.startswith("word/media/")]
    headers = [n for n in names if re.match(r"word/(header|footer)\d*\.xml", n)]
    header_txt = []
    for h in headers:
        try:
            header_txt.append(z.read(h).decode("utf-8", "replace"))
        except KeyError:
            pass

print("media files (images etc.): %d" % len(media))
print("header/footer parts: %d" % len(headers))

body = xml
# paragraph / row / cell boundaries -> whitespace + markers
body = body.replace("</w:p>", "\n")
body = body.replace("</w:tc>", "\t")
body = body.replace("</w:tr>", "\n")
body = re.sub(r"<w:tab[^>]*/>", "\t", body)
body = re.sub(r"<w:br[^>]*/>", "\n", body)
text = re.sub(r"<[^>]+>", "", body)
text = html.unescape(text)
text = re.sub(r"[ \t]+\n", "\n", text)
text = re.sub(r"\n{3,}", "\n\n", text)

out.write_text(text, encoding="utf-8")
print("chars: %d   paragraphs(non-empty): %d" % (len(text), len([l for l in text.splitlines() if l.strip()])))
print("written ->", out)
