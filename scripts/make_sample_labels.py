"""Generate a set of sample alcohol-label images for testing the verifier.

Run:  python scripts/make_sample_labels.py

Writes five PNGs into samples/, each exercising a different verifier outcome:
  01_pass.png            - everything matches  -> PASS
  02_brand_case.png      - brand differs only in case -> PASS (case ignored)
  03_wrong_abv.png       - label ABV != application -> FAIL on ABV
  04_bad_warning.png     - warning header in Title Case -> FAIL on warning
  05_blurry.png          - blur + glare, hard to read -> CANNOT VERIFY

The script also prints the application values to type into the form for each.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

# The federally mandated warning. Header in CAPS, body in sentence case.
WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car "
    "or operate machinery, and may cause health problems."
)
WARNING_FULL = "GOVERNMENT WARNING: " + WARNING_BODY


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _block(draw, x, y, text, font, max_width, gap=8):
    for line in _wrap(draw, text, font, max_width):
        draw.text((x, y), line, fill="black", font=font)
        y += font.size + gap
    return y


def make_label(filename, brand, class_type, abv, net,
               warning_header="GOVERNMENT WARNING:", blur=0.0, glare=False):
    width, height = 1000, 1400
    margin = 70
    max_width = width - 2 * margin

    img = Image.new("RGB", (width, height), "#fbf7ef")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, width - 20, height - 20], outline="#8a6d3b", width=6)

    y = 90
    brand_font = _font(76)
    bw = draw.textlength(brand, font=brand_font)
    draw.text(((width - bw) / 2, y), brand, fill="#5a3d1f", font=brand_font)
    y += 130

    y = _block(draw, margin, y, class_type, _font(40), max_width)
    y += 30
    draw.text((margin, y), abv, fill="black", font=_font(38)); y += 60
    draw.text((margin, y), net, fill="black", font=_font(38)); y += 90

    draw.line([margin, y, width - margin, y], fill="#cccccc", width=2)
    y += 40

    y = _block(draw, margin, y, warning_header, _font(30), max_width)
    y += 6
    _block(draw, margin, y, WARNING_BODY, _font(28), max_width)

    # Optional degradation for the "bad photo" sample.
    if glare:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        # Two big bright blobs covering the text band, then heavily blurred.
        od.ellipse([120, 120, 980, 760], fill=(255, 255, 255, 235))
        od.ellipse([80, 700, 920, 1150], fill=(255, 255, 255, 200))
        overlay = overlay.filter(ImageFilter.GaussianBlur(110))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    SAMPLES_DIR.mkdir(exist_ok=True)
    path = SAMPLES_DIR / filename
    img.save(path)
    return path


# (filename, on-label values, application values to type, expected outcome)
SCENARIOS = [
    {
        "file": "01_pass.png",
        "brand": "STONE'S THROW",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "abv": "45% Alc/Vol",
        "net": "750 mL",
        "app": {"brand": "STONE'S THROW", "class_type": "Kentucky Straight Bourbon Whiskey",
                "abv": "45", "net": "750 mL", "warning": WARNING_FULL},
        "expect": "All PASS",
    },
    {
        "file": "02_brand_case.png",
        "brand": "RIVERBEND RESERVE",
        "class_type": "California Red Wine",
        "abv": "13.5% Alc/Vol",
        "net": "750 mL",
        "app": {"brand": "Riverbend Reserve", "class_type": "California Red Wine",
                "abv": "13.5", "net": "750 mL", "warning": WARNING_FULL},
        "expect": "Brand PASS despite case difference -> overall PASS",
    },
    {
        "file": "03_wrong_abv.png",
        "brand": "GOLDEN OAK",
        "class_type": "Blended Scotch Whisky",
        "abv": "40% Alc/Vol",
        "net": "700 mL",
        "app": {"brand": "GOLDEN OAK", "class_type": "Blended Scotch Whisky",
                "abv": "45", "net": "700 mL", "warning": WARNING_FULL},
        "expect": "ABV FAIL (label 40% vs entered 45)",
    },
    {
        "file": "04_bad_warning.png",
        "brand": "SILVER PINE",
        "class_type": "London Dry Gin",
        "abv": "47% Alc/Vol",
        "net": "750 mL",
        "warning_header": "Government Warning:",  # title case -> should FAIL
        "app": {"brand": "SILVER PINE", "class_type": "London Dry Gin",
                "abv": "47", "net": "750 mL", "warning": WARNING_FULL},
        "expect": "Government Warning FAIL (title case, not ALL CAPS)",
    },
    {
        "file": "05_blurry.png",
        "brand": "MISTY HOLLOW",
        "class_type": "Spiced Rum",
        "abv": "35% Alc/Vol",
        "net": "750 mL",
        "blur": 9.0,
        "glare": True,
        "app": {"brand": "MISTY HOLLOW", "class_type": "Spiced Rum",
                "abv": "35", "net": "750 mL", "warning": WARNING_FULL},
        "expect": "Hard to read -> CANNOT VERIFY on one or more fields",
    },
]


def main() -> None:
    for s in SCENARIOS:
        make_label(
            s["file"], s["brand"], s["class_type"], s["abv"], s["net"],
            warning_header=s.get("warning_header", "GOVERNMENT WARNING:"),
            blur=s.get("blur", 0.0), glare=s.get("glare", False),
        )

    print("Wrote", len(SCENARIOS), "images to", SAMPLES_DIR)
    print()
    for s in SCENARIOS:
        a = s["app"]
        print("=" * 70)
        print(f"{s['file']}   (expect: {s['expect']})")
        print(f"  Brand Name:        {a['brand']}")
        print(f"  Class / Type:      {a['class_type']}")
        print(f"  Alcohol Content:   {a['abv']}")
        print(f"  Net Contents:      {a['net']}")
        print(f"  Government Warning: {a['warning']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
