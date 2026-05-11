from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import server


class ServerLogicTests(unittest.TestCase):
    def test_parse_urls_deduplicates_and_skips_invalid_tokens(self) -> None:
        urls = server.parse_urls(
            """
            https://example.com/a
            not-a-url
            https://youtu.be/abc,
            https://example.com/a
            """
        )
        self.assertEqual(urls, ["https://example.com/a", "https://youtu.be/abc"])

    def test_build_audio_command_uses_extract_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = server.normalize_settings(
                {
                    "outputDir": tmp,
                    "pythonPath": "python",
                    "ffmpegPath": "ffmpeg",
                    "denoPath": "",
                    "poServerHome": "",
                }
            )
            cmd = server.build_download_command(
                {
                    "url": "https://example.com/video",
                    "format": "mp3",
                    "audioQuality": "320",
                    "playlistMode": "single",
                    "outputDir": tmp,
                    "cookiesMode": "off",
                },
                settings,
            )
        self.assertIn("-x", cmd)
        self.assertIn("--audio-format", cmd)
        self.assertIn("mp3", cmd)
        self.assertIn("320K", cmd)
        self.assertIn("--no-playlist", cmd)

    def test_build_mp4_command_uses_height_cap_and_merge_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = server.normalize_settings(
                {
                    "outputDir": tmp,
                    "pythonPath": "python",
                    "ffmpegPath": "",
                    "denoPath": "",
                    "poServerHome": "",
                }
            )
            cmd = server.build_download_command(
                {
                    "url": "https://example.com/video",
                    "format": "mp4",
                    "videoHeight": "1080",
                    "playlistMode": "playlist",
                    "outputDir": tmp,
                    "cookiesMode": "off",
                },
                settings,
            )
        self.assertIn("--merge-output-format", cmd)
        self.assertIn("mp4", cmd)
        self.assertIn("--yes-playlist", cmd)
        self.assertTrue(any("[height<=1080]" in part for part in cmd))

    def test_youtube_command_includes_deno_and_po_http_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            po_home = str(Path(tmp) / "server")
            settings = server.normalize_settings(
                {
                    "outputDir": tmp,
                    "pythonPath": "python",
                    "ffmpegPath": "",
                    "denoPath": str(Path(tmp) / "deno.exe"),
                    "poServerHome": po_home,
                }
            )
            cmd = server.build_download_command(
                {
                    "url": "https://www.youtube.com/watch?v=abc",
                    "format": "best",
                    "videoHeight": "best",
                    "playlistMode": "single",
                    "outputDir": tmp,
                    "cookiesMode": "firefox",
                },
                settings,
            )
        self.assertIn("--js-runtimes", cmd)
        self.assertIn("youtube:player_client=mweb", cmd)
        self.assertTrue(any("youtubepot-bgutilhttp:base_url=" in part for part in cmd))
        self.assertIn("--cookies-from-browser", cmd)
        self.assertIn("firefox", cmd)

    def test_parse_progress_line_extracts_percent_speed_eta_and_file(self) -> None:
        progress = server.parse_progress_line("[download]  42.7% of 5.64MiB at 1.23MiB/s ETA 00:03")
        self.assertEqual(progress["progress"], 42.7)
        self.assertEqual(progress["speed"], "1.23MiB/s")
        self.assertEqual(progress["eta"], "00:03")
        destination = server.parse_progress_line("[ExtractAudio] Destination: C:\\Music\\track.mp3")
        self.assertEqual(destination["filePath"], "C:\\Music\\track.mp3")

    def test_queued_job_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = server.DownloadManager(server.normalize_settings({"outputDir": tmp, "concurrency": 1}))
            try:
                with manager.condition:
                    manager.active_count = 1
                job = manager.add_jobs(
                    {
                        "urls": ["https://example.com/video"],
                        "format": "mp3",
                        "outputDir": tmp,
                        "cookiesMode": "off",
                    }
                )[0]
                cancelled = manager.cancel(job["id"])
            finally:
                manager.shutdown()
        self.assertEqual(cancelled["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
