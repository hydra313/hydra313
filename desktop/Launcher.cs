using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("ConsoleHawk")]
[assembly: AssemblyDescription("ConsoleHawk desktop launcher")]
[assembly: AssemblyCompany("ConsoleHawk")]
[assembly: AssemblyProduct("ConsoleHawk")]
[assembly: AssemblyVersion("0.2.0.0")]
[assembly: AssemblyFileVersion("0.2.0.0")]

internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool checkOnly = args.Length == 1 && args[0] == "--check";
        try
        {
            string folder = AppDomain.CurrentDomain.BaseDirectory;
            string settings = Path.Combine(folder, "App", "project-path.txt");
            string project;
            if (File.Exists(settings))
                project = Path.GetFullPath(Path.Combine(folder, File.ReadAllText(settings).Trim()));
            else if (File.Exists(Path.Combine(folder, "scripts", "Start-ConsoleHawk.ps1")))
                project = folder;
            else if (File.Exists(Path.Combine(folder, "..", "scripts", "Start-ConsoleHawk.ps1")))
                project = Path.GetFullPath(Path.Combine(folder, ".."));
            else
                throw new InvalidOperationException("Keep ConsoleHawk.exe beside its App folder, or inside an installed ConsoleHawk source checkout. Rebuild the launcher if the installation has moved.");
            string script = Path.Combine(project, "scripts", "Start-ConsoleHawk.ps1");
            if (!File.Exists(script) || !File.Exists(Path.Combine(project, ".venv", "Scripts", "python.exe")))
                throw new InvalidOperationException("The ConsoleHawk application files or Python environment are missing. Restore the installation or rebuild the launcher for its new location.");
            if (checkOnly) return 0;
            if (args.Length != 0) throw new InvalidOperationException("Open ConsoleHawk.exe without command-line options.");
            bool acquired;
            using (Mutex mutex = new Mutex(true, @"Local\ConsoleHawkDesktopLauncher", out acquired))
            {
                if (!acquired) return 0;
                try
                {
                    string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
                    ProcessStartInfo start = new ProcessStartInfo(powershell);
                    start.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"";
                    start.WorkingDirectory = project;
                    start.UseShellExecute = false;
                    start.CreateNoWindow = true;
                    start.WindowStyle = ProcessWindowStyle.Hidden;
                    using (Process process = Process.Start(start))
                    {
                        if (!process.WaitForExit(90000))
                            throw new InvalidOperationException("ConsoleHawk is taking longer than expected to start. Check the application logs before trying again.");
                        if (process.ExitCode != 0)
                            throw new InvalidOperationException("ConsoleHawk could not start. Check the logs folder in the application installation, or run scripts/Start-ConsoleHawk.ps1 for details.");
                    }
                }
                finally { mutex.ReleaseMutex(); }
            }
            return 0;
        }
        catch (Exception error)
        {
            if (!checkOnly)
                MessageBox.Show(error.Message, "ConsoleHawk", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
    }
}
