"""Convert a Markdown document into plain text that a text-only LLM can read.

Markdown structure (headings, lists, tables, emphasis) is preserved: it is
lossless and helps the model follow the original document. Only two
normalisations are applied:

  * image embeds ``![alt](path)`` become an explicit text placeholder, because a
    text-only model cannot load the image file itself;
  * line endings and trailing whitespace are normalised.
"""
import pathlib
import re
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])

text = src.read_text(encoding="utf-8")

# ![alt](path "title") -> explicit text placeholder
img = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
text, n_img = img.subn(
    lambda m: "[图片：%s（文件 %s）]" % (m.group(1) or "未命名", m.group(2)), text
)

# normalise line endings + trailing whitespace
text = text.replace("\r\n", "\n").replace("\r", "\n")
text = "\n".join(line.rstrip() for line in text.split("\n"))
text = re.sub(r"\n{4,}", "\n\n\n", text)

dst.write_text(text, encoding="utf-8")

print("file       :", src.name)
print("images     :", n_img)
print("chars      :", len(text), " lines:", len(text.splitlines()))
print("written    :", dst.name)
