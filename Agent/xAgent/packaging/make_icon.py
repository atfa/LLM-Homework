"""Generate an xAgent Windows icon using Pillow (build-time only)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path(__file__).parent / "xagent.ico"
size = 256
im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((8, 8, 248, 248), radius=52, fill="#10263F")
d.rounded_rectangle((48, 63, 208, 187), radius=30, fill="#27B8E6")
d.rounded_rectangle((65, 80, 191, 170), radius=19, fill="#142C48")
d.ellipse((85, 114, 105, 134), fill="white")
d.ellipse((151, 114, 171, 134), fill="white")
d.arc((106, 130, 150, 156), start=5, end=175, fill="#27B8E6", width=6)
d.rounded_rectangle((120, 34, 136, 65), radius=6, fill="#27B8E6")
d.ellipse((114, 22, 142, 49), fill="white")
d.rounded_rectangle((31, 107, 53, 151), radius=9, fill="white")
d.rounded_rectangle((203, 107, 225, 151), radius=9, fill="white")
im.save(out, format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print(out)
