"""
Audio metadata extraction using ffprobe (structural metadata: duration,
sample rate, bitrate, channels) and ffmpeg's loudnorm/volumedetect filters
(loudness).

WHY FFMPEG/FFPROBE RATHER THAN A PYTHON AUDIO LIBRARY: the brief explicitly
expects this to be new territory ("if you have never worked with audio
before, good, neither had we"). ffprobe reliably reports container-level
metadata (duration/sample_rate/bitrate/channels) for basically any format a
browser MediaRecorder or file picker will produce (webm/opus, wav, mp3, m4a)
without needing per-format Python decoders. Loudness needs an actual decode
pass, so we shell out to ffmpeg's volumedetect filter for that.
"""

import json
import re
import subprocess
from dataclasses import dataclass


@dataclass
class AudioMetadata:
    duration_sec: float | None
    sample_rate_hz: int | None
    bitrate_kbps: float | None
    channels: int | None
    loudness_db: float | None       # mean volume, dBFS (negative = quieter)
    peak_db: float | None
    quality_note: str


def _run_ffprobe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)


def _run_volumedetect(path: str) -> tuple[float | None, float | None]:
    """
    ffmpeg's volumedetect filter decodes the whole file and reports
    mean_volume / max_volume in dBFS. We discard the actual output audio
    (-f null -) since we only want the stderr stats.
    """
    result = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    stderr = result.stderr
    mean_match = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)
    max_match = re.search(r"max_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)
    mean_db = float(mean_match.group(1)) if mean_match else None
    max_db = float(max_match.group(1)) if max_match else None
    return mean_db, max_db


def _quality_note(duration, mean_db, max_db) -> str:
    """
    Bonus: rough noise/quality estimate. This is a heuristic, not a real
    noise-floor measurement (that needs spectral analysis) -- documented
    honestly as such. Two cheap signals:
      - very low mean volume (< -40 dBFS) suggests a quiet/possibly
        silent or far-mic recording
      - a huge gap between mean and peak (> 25 dB) suggests either a
        very dynamic/noisy recording or mostly silence with a few spikes
    """
    notes = []
    if duration is not None and duration < 0.5:
        notes.append("very short clip (<0.5s) -- possibly a failed recording")
    if mean_db is not None and mean_db < -40:
        notes.append("low average loudness -- mic may have been far from speaker")
    if mean_db is not None and max_db is not None and (max_db - mean_db) > 25:
        notes.append("large gap between average and peak volume -- check for background noise or long silences")
    return "; ".join(notes) if notes else "no obvious quality issues detected"


def analyze_audio(path: str) -> AudioMetadata:
    probe = _run_ffprobe(path)
    audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    fmt = probe.get("format", {})

    duration = float(fmt["duration"]) if fmt.get("duration") else None
    bitrate_bps = float(fmt["bit_rate"]) if fmt.get("bit_rate") else None
    bitrate_kbps = round(bitrate_bps / 1000, 1) if bitrate_bps else None

    sample_rate = None
    channels = None
    if audio_streams:
        s = audio_streams[0]
        sample_rate = int(s["sample_rate"]) if s.get("sample_rate") else None
        channels = int(s["channels"]) if s.get("channels") else None

    mean_db, max_db = _run_volumedetect(path)
    note = _quality_note(duration, mean_db, max_db)

    return AudioMetadata(
        duration_sec=round(duration, 2) if duration else None,
        sample_rate_hz=sample_rate,
        bitrate_kbps=bitrate_kbps,
        channels=channels,
        loudness_db=mean_db,
        peak_db=max_db,
        quality_note=note,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("usage: python audio_analysis.py <path-to-audio-file>")
        sys.exit(1)
    meta = analyze_audio(sys.argv[1])
    print(meta)
