using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace MediaDock.Launcher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            string appRoot = AppDomain.CurrentDomain.BaseDirectory;
            string scriptPath = Path.Combine(appRoot, "Start-Downloader.ps1");

            if (!File.Exists(scriptPath))
            {
                MessageBox.Show(
                    string.Format("Could not find Start-Downloader.ps1 next to this launcher:\n\n{0}", scriptPath),
                    "MediaDock",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return;
            }

            string systemRoot = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string powershellPath = Path.Combine(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            if (!File.Exists(powershellPath))
            {
                powershellPath = "powershell.exe";
            }

            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = powershellPath,
                    Arguments = string.Format("-NoProfile -ExecutionPolicy Bypass -File \"{0}\"", scriptPath),
                    WorkingDirectory = appRoot,
                    UseShellExecute = false,
                    WindowStyle = ProcessWindowStyle.Normal,
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    string.Format("Could not start MediaDock:\n\n{0}", ex.Message),
                    "MediaDock",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }
    }
}
