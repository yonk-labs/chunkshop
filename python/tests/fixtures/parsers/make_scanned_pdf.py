"""Build a 'scanned' PDF: one page whose only content is an image XObject.

No font, no text-showing operator anywhere -- exactly what a page scanned to
image looks like to a text extractor. Hand-assembled (rather than produced by
a PDF library) so the fixture is reproducible byte-for-byte and carries no
generator-version drift.
"""
import sys, zlib

# 8x8 grayscale raster -- a diagonal stripe, so the page is demonstrably
# ink-bearing rather than a blank sheet.
W = H = 8
raw = bytes(0 if (x + y) % 3 else 255 for y in range(H) for x in range(W))
img = zlib.compress(raw)

content = b"q\n200 0 0 200 100 500 cm\n/Im0 Do\nQ\n"

objs = {}
objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
objs[3] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
           b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>")
objs[4] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
objs[5] = (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
           b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
           b"/Length %d >>\nstream\n" % (W, H, len(img)) + img + b"\nendstream")

out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
offsets = {}
for n in sorted(objs):
    offsets[n] = len(out)
    out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"

xref_at = len(out)
out += b"xref\n0 %d\n" % (len(objs) + 1)
out += b"0000000000 65535 f \n"
for n in sorted(objs):
    out += b"%010d 00000 n \n" % offsets[n]
out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_at)

open(sys.argv[1], "wb").write(bytes(out))
print(f"wrote {sys.argv[1]} ({len(out)} bytes)")
