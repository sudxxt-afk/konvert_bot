import asyncio
import sys

sys.path.insert(0, "/app")

from config import TMP_DIR
from services.converter import _to_voice


async def main():
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    src = TMP_DIR / "tone.mp3"
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        str(src),
    )
    await p.wait()
    assert src.exists(), "tone not created"
    out = await _to_voice(src)
    print("CONVERT OK:", out.name, out.stat().st_size, "bytes")
    src.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


asyncio.run(main())
