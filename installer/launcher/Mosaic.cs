// Mosaic.exe: avvia l'app desktop (python -m mosaic.desktop) dalla cartella in cui si trova, senza console.
// Compilato da installer/build_launcher.ps1 con il csc.exe di .NET Framework 4 incluso in Windows.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Mosaic")]
[assembly: System.Reflection.AssemblyProduct("Mosaic")]
[assembly: System.Reflection.AssemblyDescription("Gestore di foto e video locale")]
[assembly: System.Reflection.AssemblyVersion("2.0.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("2.0.0.0")]

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string home = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(home, @".venv\Scripts\pythonw.exe");
        if (!File.Exists(pythonw) || !File.Exists(Path.Combine(home, @"mosaic\desktop.py")))
        {
            MessageBox.Show(
                "Mosaic non è ancora installato in questa cartella.\n\n" +
                "Esegui MosaicSetup.exe (oppure setup.ps1) e poi riprova.\n\n" + home,
                "Mosaic", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }

        var info = new ProcessStartInfo(pythonw, "-m mosaic.desktop")
        {
            WorkingDirectory = home,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        info.EnvironmentVariables["MOSAIC_HOME"] = home;
        try
        {
            Process.Start(info);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("Impossibile avviare Mosaic:\n\n" + e.Message, "Mosaic",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
    }
}
