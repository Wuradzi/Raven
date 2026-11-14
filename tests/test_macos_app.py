"""scripts/make_macos_app.sh: lightweight Raven.app launcher (macOS only, no live Raven start)."""
import os
import plistlib
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("iconutil")
                     and (ROOT / ".venv/bin/python").exists(), "macOS with project .venv only")
class MacAppTests(unittest.TestCase):
    def test_bundle_is_built_into_dest_and_only_launches_run_script(self):
        with tempfile.TemporaryDirectory() as dest:
            result = subprocess.run(["bash", str(ROOT / "scripts/make_macos_app.sh"), "--dest", dest],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            app = Path(dest) / "Raven.app"
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleIdentifier"], "local.raven.assistant")
            self.assertTrue(info["NSMicrophoneUsageDescription"])
            self.assertTrue((app / "Contents/Resources/Raven.icns").stat().st_size > 1000)
            launcher = app / "Contents/MacOS/Raven"
            self.assertTrue(os.access(launcher, os.X_OK))
            dry = subprocess.run([str(launcher), "--text-only"], capture_output=True, text=True, timeout=30,
                                 env={**os.environ, "RAVEN_APP_DRY_RUN": "1"})
            self.assertEqual(dry.stdout.strip(), f"{ROOT}/scripts/run_macos.sh --text-only")
            self.assertNotIn("GROQ", launcher.read_text(encoding="utf-8"))

    def test_unknown_option_is_rejected_without_building(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/make_macos_app.sh"), "--bogus"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
