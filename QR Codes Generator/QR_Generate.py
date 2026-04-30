import qrcode
from PIL import Image, ImageOps

# =========================
# 🔗 YOUR DATA (EDIT THIS)
# =========================
data = "https://tinyurl.com/vrundavanlibrary"

# =========================
# ⚙️ QR CONFIG
# =========================
qr = qrcode.QRCode(
    version=None,  # auto size
    error_correction=qrcode.constants.ERROR_CORRECT_H,  # HIGH (for logo)
    box_size=12,  # controls overall size
    border=4,  # quiet zone (DO NOT reduce)
)

qr.add_data(data)
qr.make(fit=True)

# =========================
# 🎨 CREATE QR IMAGE
# =========================
img = qr.make_image(
    fill_color="#000000",
    back_color="#FFFFFF"
).convert("RGBA")  # use RGBA for transparency

# =========================
# 🖼️ ADD LOGO (OPTIONAL)
# =========================
logo_path = r"E:\Projects\Experiments\QR Codes Generator\image.png"

try:
    # Load logo
    logo = Image.open(logo_path).convert("RGBA")

    # Add white padding around logo (improves scan reliability)
    logo = ImageOps.expand(logo, border=20, fill='white')

    # Resize logo (SAFE SIZE: 20–25%)
    qr_width, qr_height = img.size
    logo_size = qr_width // 5   # try //4 for bigger, //6 for safer

    logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

    # Position logo at center
    pos = (
        (qr_width - logo_size) // 2,
        (qr_height - logo_size) // 2
    )

    # Paste logo with transparency mask
    img.paste(logo, pos, mask=logo)

except FileNotFoundError:
    print("⚠️ Logo not found, generating QR without logo")

# =========================
# 💾 SAVE OUTPUT
# =========================
img.save("custom_qr.png")

print("✅ QR code generated: custom_qr.png")