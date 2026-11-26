using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;

internal static class Launcher
{
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\')
            {
                slashes++;
                continue;
            }
            result.Append('\\', c == '"' ? 2 * slashes + 1 : slashes);
            result.Append(c);
            slashes = 0;
        }
        return result.Append('\\', 2 * slashes).Append('"').ToString();
    }

    private static void ForwardInput(Stream input, Stream childInput)
    {
        var buffer = new byte[8192];
        int length;
        while ((length = input.Read(buffer, 0, buffer.Length)) > 0)
        {
            childInput.Write(buffer, 0, length);
            childInput.Flush();
        }
    }

    public static int Main(string[] args)
    {
        try
        {
            string directory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string[] paths = File.ReadAllLines(Path.Combine(directory, "launcher-paths.txt"));
            if (paths.Length != 3) throw new IOException("Invalid launcher configuration.");
            var command = new StringBuilder("-B ").Append(Quote(paths[1]));
            foreach (string arg in args) command.Append(" ").Append(Quote(arg));
            var start = new ProcessStartInfo(paths[0], command.ToString());
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.EnvironmentVariables["CODEX_MONITOR_REAL_CODEX"] = paths[2];
            using (Process process = Process.Start(start))
            {
                Task.Run(() => {
                    try { ForwardInput(Console.OpenStandardInput(), process.StandardInput.BaseStream); }
                    catch (IOException) { }
                    finally { try { process.StandardInput.Close(); } catch (IOException) { } }
                });
                Task output = process.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
                Task errors = process.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
                process.WaitForExit();
                Task.WaitAll(output, errors);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Codex Session Monitor launcher: " + error.GetType().Name);
            return 1;
        }
    }
}
