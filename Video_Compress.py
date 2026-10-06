import subprocess
from pathlib import Path

FFMPEG = r"C:\Users\soham\ffmpeg-2025-03-31-git-35c091f4b7-essentials_build\bin\ffmpeg.exe"

input_file = [
    r"C:\Users\soham\Videos\Screen Recordings\Screen Recording 2026-09-11 075855.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Recording 2026-08-28 231013.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Recording 2026-09-11 072235.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Recording 2026-09-11 072604.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Recording 2026-09-11 072616.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Recording 2026-09-11 073316.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Recording 2026-09-11 073713.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Screen Recording 2026-07-15 150511.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Screen Recording 2026-07-24 150835.mp4",
    r"C:\Users\soham\Videos\Screen Recordings\Screen Recording 2026-08-28 231008.mp4"
]


i = 0
while i < len(input_file):
    output_file = str(Path(input_file[i]).with_stem(
        Path(input_file[i]).stem + "_1080p"
    )) + ".mp4"

    cmd = [
        FFMPEG,
        "-i", input_file[i],
        "-vf", "scale=-2:1080",
        "-c:v", "libx265",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "copy",
        output_file
    ]

    subprocess.run(cmd, check=True)
    print("Done")
    i += 1