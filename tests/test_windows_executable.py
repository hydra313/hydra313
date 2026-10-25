"""Build and validate the actual Windows GUI executable without opening UI."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows executable')

@pytest.fixture(scope='module')
def launcher(tmp_path_factory):
 dest=tmp_path_factory.mktemp('Console Hawk')
 result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/Build-Launcher.ps1'),'-Destination',str(dest)],cwd=ROOT,capture_output=True,text=True,timeout=60)
 assert result.returncode==0,result.stdout+result.stderr
 return dest

def test_executable_contains_logo_and_validates_installed_app(launcher):
 exe=launcher/'ConsoleHawk.exe'
 assert exe.read_bytes()[:2]==b'MZ'
 assert subprocess.run([str(exe),'--check'],timeout=15).returncode==0
 script="Add-Type -AssemblyName System.Drawing; $i=[Drawing.Icon]::ExtractAssociatedIcon('"+str(exe).replace("'","''")+"'); if (-not $i -or $i.Width -lt 16) { exit 1 }; $i.Dispose(); if ((Get-Item -LiteralPath '"+str(exe).replace("'","''")+"').VersionInfo.ProductName -ne 'ConsoleHawk') { exit 2 }"
 assert subprocess.run(['powershell.exe','-NoProfile','-Command',script],timeout=15).returncode==0

def test_missing_configuration_returns_error_without_starting_browser(launcher,tmp_path):
 exe=tmp_path/'ConsoleHawk.exe'; shutil.copy2(launcher/'ConsoleHawk.exe',exe)
 assert subprocess.run([str(exe),'--check'],timeout=15).returncode==2

def test_missing_runtime_returns_error_without_starting_browser(launcher,tmp_path):
 exe=tmp_path/'ConsoleHawk.exe'; shutil.copy2(launcher/'ConsoleHawk.exe',exe)
 (tmp_path/'App').mkdir(); (tmp_path/'App/project-path.txt').write_text(str(tmp_path/'missing'),encoding='utf-8')
 assert subprocess.run([str(exe),'--check'],timeout=15).returncode==2


def test_repo_executable_finds_parent_installation(launcher,tmp_path):
 (tmp_path/'desktop').mkdir(); (tmp_path/'scripts').mkdir(); (tmp_path/'.venv/Scripts').mkdir(parents=True)
 (tmp_path/'scripts/Start-ConsoleHawk.ps1').touch(); (tmp_path/'.venv/Scripts/python.exe').touch()
 exe=tmp_path/'desktop/ConsoleHawk.exe'; shutil.copy2(launcher/'ConsoleHawk.exe',exe)
 assert subprocess.run([str(exe),'--check'],timeout=15).returncode==0
