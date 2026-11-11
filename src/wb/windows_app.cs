using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class WBMDesktop {
    private static readonly string Python = __PYTHON__;
    private static readonly string Session = Guid.NewGuid().ToString();
    private static string Url;
    private static string Helper(string action, bool owned) {
        var info = new ProcessStartInfo(Python, "-m wb.desktop " + action +
            (owned ? " --session " + Session + " --owner-pid " + Process.GetCurrentProcess().Id : ""));
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8;
        info.StandardErrorEncoding = Encoding.UTF8;
        info.WorkingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        using (var process = Process.Start(info)) {
            // Drain both streams while the helper runs; never deadlock on an error pipe.
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(25000)) { process.Kill(); throw new Exception("起動確認が時間切れになりました"); }
            if (process.ExitCode != 0) throw new Exception(error.Result.Trim());
            return output.Result.Trim();
        }
    }
    private static void Browse() {
        if (!String.IsNullOrEmpty(Url)) Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true });
    }
    [STAThread]
    private static void Main() {
        string home = Environment.GetEnvironmentVariable("WBM_HOME") ??
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "WBM");
        string key;
        using (var hash = SHA256.Create()) key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(home.ToLowerInvariant()))).Replace("-", "");
        bool first;
        using (var instance = new Mutex(true, @"Local\WBMDesktop-" + key, out first)) {
            if (!first) {
                // The running app keeps the one server; a second launch only opens its page.
                try { Helper("show", false); } catch { }
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var form = new Form { Text = "WBM", ClientSize = new Size(360, 145),
                StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
                MaximizeBox = false };
            var icon = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "WBM.ico");
            if (File.Exists(icon)) form.Icon = new Icon(icon);
            var label = new Label { Text = "サーバーを起動しています…", AutoSize = false,
                Location = new Point(20, 20), Size = new Size(320, 45) };
            var open = new Button { Text = "画面を開く", Enabled = false, Location = new Point(20, 90), Width = 150 };
            var quit = new Button { Text = "終了", Location = new Point(190, 90), Width = 150 };
            form.Controls.AddRange(new Control[] { label, open, quit });
            open.Click += delegate { Browse(); };
            quit.Click += delegate { form.Close(); };
            form.FormClosing += delegate {
                try { Helper("stop", true); } catch { }
            };
            var worker = new System.ComponentModel.BackgroundWorker();
            worker.DoWork += delegate { Url = Helper("start", true); Helper("browse", true); };
            worker.RunWorkerCompleted += delegate(object sender, System.ComponentModel.RunWorkerCompletedEventArgs args) {
                if (form.IsDisposed) return;
                if (args.Error != null) {
                    MessageBox.Show(args.Error.Message, "WBMを起動できませんでした", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    form.Close(); return;
                }
                label.Text = "サーバーは起動中です。\nこのアプリを終了すると停止します。";
                open.Enabled = true;
            };
            form.Shown += delegate { worker.RunWorkerAsync(); };
            Application.Run(form);
            instance.ReleaseMutex();
        }
    }
}
