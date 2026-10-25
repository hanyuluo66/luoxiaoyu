// 落小鱼引擎 — creates and holds a Windows RDP "child session".
//
// A child session is a second, fully logged-in Windows session with its own
// input queue. Running WeChat + the bot inside it means UI automation there
// never moves the mouse or steals focus from the person using the console
// session. It needs no hook, no injection and no specific WeChat version.
//
// The connection uses IMsRdpExtendedSettings.ConnectToChildSession, so no RDP
// listener on port 3389 is required (works on Windows Home editions).
//
// Control channel (127.0.0.1:18999, one command per line):
//   status  -> {"connected":0|1|2,"child":<id|-1>}
//   show    -> show the desktop viewer window (for QR login / manual control)
//   hide    -> hide it again (the session keeps running)
//   quit    -> exit the keeper (the child session is logged off by Windows)
//   logoff  -> log the child session off explicitly
//
// Build:
//   csc /nologo /target:winexe /platform:x64 /r:System.Windows.Forms.dll ^
//       /r:System.Drawing.dll /out:落小鱼引擎.exe 落小鱼引擎.cs
using System;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    private const string RdpClientClsid = "A0C63C30-F08D-4AB4-907C-34905D770C7D";
    private const int ControlPort = 18999;

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSEnableChildSessions(bool enable);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSIsChildSessionsEnabled(out bool enabled);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSGetChildSessionId(out uint sessionId);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSLogoffSession(IntPtr server, uint sessionId, bool wait);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    private const uint NoChildSession = uint.MaxValue;
    private static string _logPath;
    private static readonly object LogLock = new object();
    internal static volatile HiddenForm Form;

    internal static void Log(string message)
    {
        try
        {
            lock (LogLock)
            {
                File.AppendAllText(
                    _logPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine);
            }
        }
        catch
        {
        }
    }

    internal static int ChildSessionId()
    {
        uint id;
        if (!WTSGetChildSessionId(out id) || id == NoChildSession)
        {
            return -1;
        }
        return unchecked((int)id);
    }

    [STAThread]
    private static int Main(string[] args)
    {
        _logPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "childsession-keeper.log");
        try { SetProcessDPIAware(); } catch { }
        Log("启动: " + string.Join(" ", args));

        bool logoff = false, stop = false;
        foreach (var arg in args)
        {
            if (arg == "--logoff") { logoff = true; stop = true; }
            if (arg == "--stop") { stop = true; }
        }

        if (stop)
        {
            int existing = ChildSessionId();
            if (existing < 0)
            {
                Log("没有子会话");
                return 0;
            }
            if (logoff)
            {
                Log("注销子会话 " + existing + " => " + WTSLogoffSession(IntPtr.Zero, (uint)existing, true));
            }
            return 0;
        }

        bool enabled;
        if (WTSIsChildSessionsEnabled(out enabled))
        {
            if (!enabled)
            {
                Log("启用 Child Sessions => " + WTSEnableChildSessions(true));
            }
        }

        StartControlServer();

        var form = new HiddenForm();
        Form = form;
        Application.Run(form);
        Log("退出");
        return 0;
    }

    private static void StartControlServer()
    {
        var thread = new Thread(() =>
        {
            TcpListener listener = null;
            try
            {
                listener = new TcpListener(IPAddress.Loopback, ControlPort);
                listener.Start();
                Log("控制通道已监听 127.0.0.1:" + ControlPort);
            }
            catch (Exception ex)
            {
                Log("控制通道启动失败: " + ex.Message);
                return;
            }

            while (true)
            {
                try
                {
                    using (var client = listener.AcceptTcpClient())
                    using (var stream = client.GetStream())
                    using (var reader = new StreamReader(stream, Encoding.UTF8))
                    using (var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true })
                    {
                        var line = reader.ReadLine();
                        if (line == null) continue;
                        var reply = HandleCommand(line.Trim().ToLowerInvariant());
                        writer.WriteLine(reply);
                    }
                }
                catch (Exception ex)
                {
                    Log("控制通道异常: " + ex.Message);
                    Thread.Sleep(500);
                }
            }
        });
        thread.IsBackground = true;
        thread.Start();
    }

    private static string HandleCommand(string command)
    {
        var form = Form;
        switch (command)
        {
            case "status":
                return "{\"connected\":" + (form == null ? 0 : form.ConnectedState)
                       + ",\"child\":" + ChildSessionId() + "}";
            case "show":
                if (form != null) form.Invoke((MethodInvoker)(() => form.ShowViewer()));
                Log("收到 show");
                return "ok";
            case "hide":
                if (form != null) form.Invoke((MethodInvoker)(() => form.HideViewer()));
                Log("收到 hide");
                return "ok";
            case "logoff":
                {
                    int id = ChildSessionId();
                    if (id >= 0)
                    {
                        Log("注销子会话 " + id + " => " + WTSLogoffSession(IntPtr.Zero, (uint)id, true));
                    }
                    return "ok";
                }
            case "quit":
                Log("收到 quit");
                if (form != null) form.Invoke((MethodInvoker)(() => form.Quit()));
                return "ok";
            default:
                return "unknown";
        }
    }
}

internal sealed class HiddenForm : Form
{
    private readonly KeeperHost _host;
    private readonly System.Windows.Forms.Timer _poll;
    private int _ticks;
    private bool _readyLogged;
    private bool _showing;
    private bool _autoLoginTried;

    // ---------------- 自动输入密码（分身登录框） ----------------
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct CREDENTIALW
    {
        public uint Flags;
        public uint Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct KEYBDINPUT
    {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Explicit, Size = 32)]
    internal struct INPUT
    {
        [FieldOffset(0)] public uint type;
        [FieldOffset(8)] public KEYBDINPUT ki;
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredReadW(string target, uint type, uint flags, out IntPtr credentialPtr);

    [DllImport("advapi32.dll")]
    private static extern void CredFree(IntPtr cred);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, INPUT[] inputs, int size);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    private static string ReadStoredPassword()
    {
        IntPtr ptr = IntPtr.Zero;
        try
        {
            if (!CredReadW("TERMSRV/localhost", 1, 0, out ptr)) return null;
            CREDENTIALW cred = (CREDENTIALW)Marshal.PtrToStructure(ptr, typeof(CREDENTIALW));
            if (cred.CredentialBlobSize == 0) return null;
            return Marshal.PtrToStringUni(cred.CredentialBlob, (int)cred.CredentialBlobSize / 2);
        }
        catch (Exception ex)
        {
            Program.Log("读取凭据失败: " + ex.Message);
            return null;
        }
        finally
        {
            if (ptr != IntPtr.Zero) CredFree(ptr);
        }
    }


    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")]
    private static extern IntPtr SetFocus(IntPtr hWnd);

    /// <summary>强制把窗口抢到前台（SetForegroundWindow 单独用会被系统拒绝）。</summary>
    private static void ForceForeground(IntPtr hwnd)
    {
        IntPtr fg = GetForegroundWindow();
        uint tidFg = GetWindowThreadProcessId(fg, IntPtr.Zero);
        uint tidMe = GetCurrentThreadId();
        uint tidTarget = GetWindowThreadProcessId(hwnd, IntPtr.Zero);
        AttachThreadInput(tidMe, tidFg, true);
        AttachThreadInput(tidMe, tidTarget, true);
        BringWindowToTop(hwnd);
        SetForegroundWindow(hwnd);
        SetFocus(hwnd);
        System.Threading.Thread.Sleep(300);
        AttachThreadInput(tidMe, tidFg, false);
        AttachThreadInput(tidMe, tidTarget, false);
    }

    private static void SendUnicodeText(string text)
    {
        foreach (char ch in text)
        {
            INPUT down = new INPUT();
            down.type = 1;
            down.ki.wScan = ch;
            down.ki.dwFlags = 0x0004;
            SendInput(1, new INPUT[] { down }, Marshal.SizeOf(typeof(INPUT)));

            INPUT up = new INPUT();
            up.type = 1;
            up.ki.wScan = ch;
            up.ki.dwFlags = 0x0004 | 0x0002;
            SendInput(1, new INPUT[] { up }, Marshal.SizeOf(typeof(INPUT)));
            System.Threading.Thread.Sleep(40);
        }
    }

    private static void SendEnter()
    {
        INPUT down = new INPUT();
        down.type = 1;
        down.ki.wVk = 0x0D;
        SendInput(1, new INPUT[] { down }, Marshal.SizeOf(typeof(INPUT)));

        INPUT up = new INPUT();
        up.type = 1;
        up.ki.wVk = 0x0D;
        up.ki.dwFlags = 0x0002;
        SendInput(1, new INPUT[] { up }, Marshal.SizeOf(typeof(INPUT)));
    }
    private int _connectingSince;

    internal int ConnectedState
    {
        get
        {
            try { return _host.ConnectedState; }
            catch { return 0; }
        }
    }

    public HiddenForm()
    {
        Text = "落小鱼 桌面分身";
        ShowInTaskbar = true;
        StartPosition = FormStartPosition.Manual;
        Location = new Point(60, 60);
        Size = new Size(1280, 752);
        MinimumSize = new Size(480, 300);

        _host = new KeeperHost();
        _host.Dock = DockStyle.Fill;
        Controls.Add(_host);

        Shown += (s, e) =>
        {
            HideViewer();
            try
            {
                _host.Connect(1280, 720);
            }
            catch (Exception ex)
            {
                Program.Log("连接异常: " + ex.Message);
            }
        };

        _poll = new System.Windows.Forms.Timer { Interval = 1000 };
        _poll.Tick += OnTick;
        _poll.Start();
    }

    private void OnTick(object sender, EventArgs e)
    {
        _ticks++;
        int connected = ConnectedState;
        int sid = Program.ChildSessionId();

        if (connected == 1 && !_readyLogged)
        {
            _readyLogged = true;
            Program.Log("READY 子会话=" + sid);
        }
        if (connected == 0 && _readyLogged)
        {
            _readyLogged = false;
            Program.Log("连接断开，准备重连");
        }

        if (_ticks % 15 == 1)
        {
            Program.Log("状态: RDP=" + connected + " 子会话=" + sid);
        }

        // 卡在"连接中"(2)时 ActiveX 不会自己收尾（上一个子会话注销不干净时常见）。
        // 20 秒还没连上，多半是子会话在等 Windows 登录（CredUI）——把画面露出来让人输密码。
        // 之后**不再强制断开**：断开会把用户正在输入的密码清空（"登录没有成功"的元凶）。
        if (connected == 2)
        {
            if (_connectingSince == 0)
            {
                _connectingSince = _ticks;
            }
            else if (_ticks - _connectingSince == 20 && !_showing)
            {
                Program.Log("连接中超过 20 秒，显示窗口（需要在分身里输入 Windows 密码）");
                ShowViewer();
            }
            else if (_ticks - _connectingSince == 35 && !_autoLoginTried)
            {
                _autoLoginTried = true;
                try
                {
                    string pw = ReadStoredPassword();
                    if (pw == null || pw.Length == 0)
                    {
                        Program.Log("没有可用凭据，等用户手动输入密码");
                    }
                    else
                    {
                        Program.Log("尝试自动输入保存的密码…");
                        ShowViewer();
                        System.Threading.Thread.Sleep(600);
                        ForceForeground(Handle);
                        System.Threading.Thread.Sleep(1200);
                        Program.Log("前台已强制，开始输入密码");
                        SendUnicodeText(pw);
                        System.Threading.Thread.Sleep(400);
                        SendEnter();
                        System.Threading.Thread.Sleep(2500);
                        // 再补一次（第一次可能输入框还没就绪）
                        string again = ReadStoredPassword();
                        if (again != null && again.Length > 0 && _host.ConnectedState == 2)
                        {
                            Program.Log("仍在连接中，补输一次密码");
                            ForceForeground(Handle);
                            System.Threading.Thread.Sleep(800);
                            SendUnicodeText(again);
                            System.Threading.Thread.Sleep(300);
                            SendEnter();
                        }
                    }
                }
                catch (Exception ex) { Program.Log("自动登录异常: " + ex.Message); }
            }
            else if (_ticks - _connectingSince == 90)
            {
                Program.Log("仍在等待登录：请在弹出的窗口里输入密码，登录成功后会自动隐藏");
            }
            else if (_ticks - _connectingSince == 300)
            {
                // 5 分钟还没成功，才考虑重来一次（此时用户多半不在电脑前）
                Program.Log("等待登录超过 5 分钟，重新发起连接");
                _connectingSince = 0;
                try { _host.Disconnect(); }
                catch (Exception ex) { Program.Log("强制断开异常: " + ex.Message); }
                try { _host.Connect(1280, 720); }
                catch (Exception ex) { Program.Log("重连异常: " + ex.Message); }
            }
        }
        else
        {
            _connectingSince = 0;
        }

        if (connected == 0 && _ticks % 20 == 0)
        {
            try
            {
                _host.Connect(1280, 720);
            }
            catch (Exception ex)
            {
                Program.Log("重连异常: " + ex.Message);
            }
        }
    }

    internal void ShowViewer()
    {
        _showing = true;
        ShowInTaskbar = true;
        Show();
        WindowState = FormWindowState.Normal;
        Activate();
    }

    internal void HideViewer()
    {
        _showing = false;
        Hide();
    }

    internal void Quit()
    {
        _poll.Stop();
        try { _host.Disconnect(); } catch { }
        Application.Exit();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (e.CloseReason == CloseReason.UserClosing)
        {
            // Closing the window only hides it: the session must stay alive.
            e.Cancel = true;
            HideViewer();
            Program.Log("窗口关闭 -> 已隐藏（会话保持）");
            return;
        }
        base.OnFormClosing(e);
    }

    internal bool IsShowing { get { return _showing; } }
}

internal sealed class KeeperHost : AxHost
{
    internal KeeperHost() : base("A0C63C30-F08D-4AB4-907C-34905D770C7D") { }

    internal int ConnectedState
    {
        get
        {
            if (!IsHandleCreated) return 0;
            return Convert.ToInt32(GetProp(Ocx(), "Connected"));
        }
    }


    internal void Connect(int width, int height)
    {
        var client = Ocx();
        SetProp(client, "Server", "localhost");
        SetProp(client, "DesktopWidth", width);
        SetProp(client, "DesktopHeight", height);
        SetProp(client, "ColorDepth", 32);
        // 账户要拆成「域 + 用户名」两段：整串 "MicrosoftAccount\邮箱" 会被 Windows
        // 当成一个用户名，登录必然报「用户名或密码错误」。已实测：
        //   ❌ UserName="MicrosoftAccount\邮箱"
        //   ✅ Domain="MicrosoftAccount", UserName="邮箱"
        string acctDomain = "MicrosoftAccount";
        string acctUser = "";
        try
        {
            string accountFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "childsession-account.txt");
            if (File.Exists(accountFile))
            {
                string[] lines = File.ReadAllLines(accountFile);
                for (int i = 0; i < lines.Length; i++)
                {
                    string line = lines[i].Trim();
                    if (line.StartsWith("domain=")) acctDomain = line.Substring(7).Trim();
                    else if (line.StartsWith("user=")) acctUser = line.Substring(5).Trim();
                    else if (line.StartsWith("MicrosoftAccount\\"))
                    {
                        acctDomain = "MicrosoftAccount";
                        acctUser = line.Substring("MicrosoftAccount\\".Length);
                    }
                }
            }
        }
        catch (Exception ex) { Program.Log("读取账户配置失败: " + ex.Message); }
        // 不显式设置 UserName/Domain：让 RDP 用 CredSSP 单点登录
        // （凭据管理器里已有 TERMSRV/localhost，设了用户名反而会弹出登录框）
        Program.Log("RDP 账户交给系统（SSO），凭据库里的账户: " + acctDomain + "\\" + acctUser);
        SetProp(client, "ConnectingText", "正在创建桌面分身…");
        SetProp(client, "DisconnectedText", "桌面分身已断开");

        var secured = GetProp(client, "SecuredSettings2");
        if (secured != null)
        {
            SetProp(secured, "KeyboardHookMode", 1);
            SetProp(secured, "AudioRedirectionMode", 2);
        }

        var advanced = GetProp(client, "AdvancedSettings7") ?? GetProp(client, "AdvancedSettings2");
        if (advanced != null)
        {
            SetProp(advanced, "RDPPort", 3389);
            // 关掉 NLA/CredSSP：连本机子会话时它会把已保存的凭据判成"不工作"并弹窗
            SetProp(advanced, "EnableCredSspSupport", true);
            SetProp(advanced, "EnableWindowsKey", 1);
            SetProp(advanced, "SmartSizing", true);
        }

        object enableChild = true;
        try
        {
            ((IMsRdpExtendedSettings)client).set_Property("ConnectToChildSession", ref enableChild);
            Program.Log("已设置 ConnectToChildSession=true");
        }
        catch (Exception ex)
        {
            Program.Log("设置 ConnectToChildSession 失败: " + ex.Message);
        }

        Invoke(client, "Connect");
        Program.Log("已调用 RDP Connect()");
    }

    internal void Disconnect()
    {
        try
        {
            if (IsHandleCreated) Invoke(Ocx(), "Disconnect");
        }
        catch
        {
        }
    }

    private object Ocx()
    {
        var ocx = GetOcx();
        if (ocx == null)
        {
            var force = Handle;
            if (force == IntPtr.Zero) { }
            ocx = GetOcx();
        }
        if (ocx == null) throw new InvalidOperationException("RDP ActiveX 未初始化");
        return ocx;
    }

    private static object GetProp(object target, string name)
    {
        return target.GetType().InvokeMember(name, BindingFlags.GetProperty, null, target, null);
    }

    private static void SetProp(object target, string name, object value)
    {
        target.GetType().InvokeMember(name, BindingFlags.SetProperty, null, target, new[] { value });
    }

    private static object Invoke(object target, string name)
    {
        return target.GetType().InvokeMember(name, BindingFlags.InvokeMethod, null, target, null);
    }

    [ComImport]
    [Guid("302D8188-0052-4807-806A-362B628F9AC5")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMsRdpExtendedSettings
    {
        void set_Property(
            [In, MarshalAs(UnmanagedType.BStr)] string propertyName,
            [In, MarshalAs(UnmanagedType.Struct)] ref object value);

        [return: MarshalAs(UnmanagedType.Struct)]
        object get_Property([In, MarshalAs(UnmanagedType.BStr)] string propertyName);
    }
}
