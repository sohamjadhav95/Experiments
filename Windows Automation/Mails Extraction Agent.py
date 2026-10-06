
import pyautogui
import pyautogui
import time
import pyperclip
from pathlib import Path
import shutil
from PIL import Image
from urllib.parse import urlparse

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.2


tab_clicks = 3

def clean_screenshots():
    SOURCE = Path(r"C:\Users\soham\OneDrive\Pictures\Screenshots")
    DESTINATION = SOURCE / "Archives"

    DESTINATION.mkdir(exist_ok=True)

    for item in SOURCE.iterdir():
        if item.name == "Archives":
            continue  # Don't move the Archives folder itself

        shutil.move(str(item), str(DESTINATION / item.name))

    print("Everything moved to Archives.")

def login_logout():
    pyautogui.press('win')
    time.sleep(1)
    pyautogui.typewrite("https://contactout.com/profile", interval=0.02)
    pyautogui.press('enter')
    time.sleep(5)
    
    pyautogui.click(696, 1080)
    time.sleep(5)

    pyautogui.press('win')
    time.sleep(1)
    pyautogui.typewrite("https://contactout.com/login", interval=0.02)
    pyautogui.press('enter')
    time.sleep(5)

    pyautogui.click(1428, 680)
    time.sleep(5)

    pyautogui.click(895, 1067)
    time.sleep(0.5)

    for _ in range(tab_clicks):
        pyautogui.press('tab')
        time.sleep(1)
    pyautogui.press('enter')

    time.sleep(5)

    pyautogui.hotkey('ctrl', 'shift', 'w')

def add_connection(linkedin_url):
    username = urlparse(linkedin_url).path.rstrip("/").split("/")[-1]

    invite_url = (
        f"https://www.linkedin.com/preload/custom-invite/?vanityName={username}"
    )

    pyautogui.press("win")
    time.sleep(1)

    pyautogui.write(invite_url, interval=0.02)
    pyautogui.press("enter")
    time.sleep(5)

    pyautogui.press("tab", presses=3, interval=0.5)
    pyautogui.press("enter")

def screenshots_to_pdf(screenshots_folder, output_pdf):
    screenshots_folder = Path(screenshots_folder)

    # Supported image formats
    extensions = {".png", ".jpg", ".jpeg", ".bmp"}

    images = sorted(
        [f for f in screenshots_folder.iterdir()
         if f.is_file() and f.suffix.lower() in extensions]
    )

    if not images:
        print("No screenshots found.")
        return

    pdf_images = []

    for img_path in images:
        img = Image.open(img_path)

        # Convert RGBA/P mode images to RGB for PDF
        if img.mode != "RGB":
            img = img.convert("RGB")

        pdf_images.append(img)

    # Save first image and append the rest
    pdf_images[0].save(
        output_pdf,
        save_all=True,
        append_images=pdf_images[1:]
    )

    print(f"PDF saved to: {output_pdf}")

def upload_pdf_to_gemini(pdf_path):
    prompt = (
        "Extract all email IDs from the photos present in this PDF. "
        "Return the results in a text box in the following format:\n\n"
        "Name | Email ID | Email ID 2\n\n"
        "If a second email is not available, leave it blank."
    )

    # Copy PDF path
    pyperclip.copy(pdf_path)

    # Open Start menu
    pyautogui.press("win")
    time.sleep(1)

    # Open Gemini
    pyautogui.write("Gemini", interval=0.03)
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(5)

    # Navigate to upload
    pyautogui.press("tab", presses=3, interval=0.5)
    pyautogui.press("enter")
    time.sleep(1)
    pyautogui.press("enter")
    time.sleep(2)

    # Paste PDF path
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(5)

    # Type prompt
    pyautogui.write(prompt, interval=0.01)
    time.sleep(1)

    # Send prompt
    pyautogui.press("enter")

def read_links(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

links = read_links(r"E:\Projects\Experiments\Windows Automation\url.txt")

clean_screenshots()

for i, link in enumerate(links, start=1):
    pyperclip.copy(link)
    print(f"Processing {i}: {link}")
    time.sleep(1)

    pyautogui.press('win')
    time.sleep(1)

    pyautogui.hotkey("ctrl", "v")
    time.sleep(1)
    pyautogui.press("enter")
    time.sleep(10)

    pyautogui.click(2419, 118)
    time.sleep(2)
    
    for _ in range(4):
        pyautogui.press('tab')
        time.sleep(1)
    pyautogui.press('enter')
    time.sleep(4)

    pyautogui.click(2216, 434)
    time.sleep(2)
    pyautogui.click(2067, 1162)

    pyautogui.keyDown('win')
    time.sleep(0.2)
    pyautogui.press('printscreen')
    time.sleep(0.2)
    pyautogui.keyUp('win')
    time.sleep(4)
    
    add_connection(link)
    time.sleep(2)
    
    # After every 5 links
    if i % 5 == 0 and i != len(links):
        print("Processed 5 links. Logging out/in...")
        login_logout()


pdf_path = r"E:\Projects\Experiments\Windows Automation\screenshots.pdf"

pdf_file = Path(pdf_path)
if pdf_file.exists():
    pdf_file.unlink()

screenshots_to_pdf(
    r"C:\Users\soham\OneDrive\Pictures\Screenshots",
    pdf_path
)

upload_pdf_to_gemini(pdf_path)
