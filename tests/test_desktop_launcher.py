"""Exercise the Windows app-window launcher without opening real windows."""
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows launcher")

def run_script(script):
    result = subprocess.run([POWERSHELL, "-NoProfile", "-Command", "$ErrorActionPreference='Stop'; " + script], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout

def test_desktop_opens_app_window_with_quoted_profile_and_no_browser_tab(tmp_path):
    fake = tmp_path / "Edge Application" / "msedge.exe"
    fake.parent.mkdir()
    fake.touch()
    output = run_script(fr"""
. ./scripts/Desktop-Window.ps1
function Get-ConsoleHawkBrowserPath {{ '{fake}' }}
function Start-Process {{ param($FilePath, $ArgumentList) Write-Output $FilePath; Write-Output $ArgumentList }}
Start-ConsoleHawkWindow -ProjectRoot 'C:\Console Hawk' -Url 'http://127.0.0.1:8000/'
""")
    assert str(fake) in output
    assert '--app=http://127.0.0.1:8000/' in output
    assert r'--user-data-dir="C:\Console Hawk\.desktop-profile"' in output
    assert '--window-size=1440,960' in output
    assert '--disable-web-security' not in output

def test_missing_browser_reports_actionable_error():
    output = run_script(r"""
. ./scripts/Desktop-Window.ps1
try { Get-ConsoleHawkBrowserPath -Candidates @('Z:\missing\msedge.exe'); throw 'Did not fail' }
catch { if ($_.Exception.Message -notmatch 'Install Microsoft Edge or Google Chrome') { throw }; Write-Output 'expected error' }
""")
    assert 'expected error' in output
