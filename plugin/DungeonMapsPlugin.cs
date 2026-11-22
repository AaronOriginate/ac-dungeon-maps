using System.Globalization;
using System.Numerics;
using System.Text.Json;
using System.Text.Json.Nodes;
using AcDream.Plugin.Abstractions;

namespace AcDungeonMaps;

/// <summary>
/// Shows the dungeon maps in game: a map window for the dungeon the character is in (its level,
/// the chosen route, goals, portals, doors and the character), and the route drawn on the floor.
/// </summary>
public sealed class DungeonMapsPlugin : IAcDreamPlugin
{
    // Remaster palette: dark leather, gilt edges, parchment text.
    static readonly PluginColor Ink = new(13, 8, 5, 232), Leather = new(31, 22, 14, 240), GoldLo = new(107, 79, 36),
        Gold = new(201, 162, 90), GoldHi = new(244, 220, 156), Text = new(234, 223, 198), Dim = new(168, 153, 124),
        RouteAhead = new(255, 214, 120), RouteBehind = new(244, 220, 156, 120), RouteOff = new(244, 220, 156, 55);
    const uint LineColour = 0xFFD678;

    static readonly (string Name, int W, int H)[] Sizes = [("small", 260, 300), ("medium", 320, 380), ("large", 440, 520)];
    const int TitleH = 22, FooterH = 60;

    IPluginHost _host = null!;
    string _dir = "";
    readonly Dictionary<uint, IndexEntry> _index = [];
    readonly List<IDisposable> _registrations = [];
    IPluginCanvas? _canvas;
    string _keyName = "/dmap";
    IPluginWorldLineLayer? _lines;
    bool _linesDirty;
    Settings _settings = new();
    bool _settingsDirty;

    // The dungeon being shown.
    Dungeon? _dungeon;
    uint _landblock;            // landblock key (high half), 0 when not in a mapped dungeon
    uint _seenLandblock = uint.MaxValue;
    int _route;
    int _loadedBand = -1;
    int? _manualBand;           // a level picked by hand, until right-click or a level change underfoot
    int _playerBand = -1;
    PluginImage _bandImage;
    bool _userClosed;           // closed in this dungeon; opens again in the next one
    readonly Dictionary<string, PluginImage> _icons = [];
    bool _iconsLoaded;

    // View.
    Vector2 _center;
    bool _follow = true;
    Vector3 _player;
    float _heading;
    bool _refreshProgress;
    float _progress;
    Vector3 _lastPaintedPlayer;
    float _lastPaintedHeading;
    int _tick;

    // 3D view: rendered off the tick thread, shown as an image, with the route and markers painted over it.
    readonly Renderer3D _renderer = new();
    Mesh3D? _mesh, _renderingMesh;
    Task<Frame3D>? _rendering;
    RenderJob _lastJob;
    bool _hasJob;
    long _lastCameraChange;
    Frame3D? _frame;
    PluginImage _frameImage;
    int _frameSerial;
    float _yaw, _pitch = 0.96f;
    Vector3 _target3;
    bool _renderFailed;

    // Pointer.
    enum Drag { None, Window, Pan, Orbit, Right }
    Drag _drag;
    bool _rightMoved;
    PluginPoint _pressAt, _dragLatest;
    PluginRect _rClose, _rPrev, _rNext, _rUp, _rDown, _rTitle, _rMap, _r3d;

    sealed class Settings
    {
        public double OffsetX { get; set; } = -12;
        public double OffsetY { get; set; } = 160;
        public int Size { get; set; } = 1;
        public bool Lines { get; set; } = true;
        public float Zoom { get; set; } = 2f;
        public bool View3D { get; set; } = true;
        public float Dist3 { get; set; } = 55f;
        public Dictionary<string, int> Routes { get; set; } = [];
    }

    public void Initialize(IPluginHost host)
    {
        _host = host;
        _dir = host.PluginDirectory ?? AppContext.BaseDirectory;
        string indexPath = Path.Combine(_dir, "data", "index.json");
        using var index = File.OpenRead(indexPath);
        using var doc = JsonDocument.Parse(index);
        foreach (JsonElement e in doc.RootElement.EnumerateArray())
        {
            string? lb = e.GetProperty("lb").GetString();
            if (lb is not { Length: 4 } || !ushort.TryParse(lb, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out ushort key))
                throw new InvalidDataException("Invalid landblock in map index");
            _index[key] = new IndexEntry(lb, e.GetProperty("slug").GetString() ?? "",
                e.GetProperty("title").GetString() ?? lb, e.GetProperty("group").GetString() ?? "");
        }
        LoadSettings();
        host.Log.Info($"{_index.Count} dungeon maps loaded");
    }

    public void Enable()
    {
        _host.Events.Tick += OnTick;
        _host.Events.LoginComplete += OnLogin;
        _host.Ui.ThemeChanged += OnTheme;
        _registrations.Add(_host.Commands.Register("dmap", OnCommand));
        // The first chord the client hasn't taken. Ctrl+M already belongs to the client.
        _keyName = "/dmap";
        foreach ((PluginKeyChord chord, string name) in new[] {
            (new PluginKeyChord(PluginKey.M, Alt: true), "Alt+M"), (new PluginKeyChord(PluginKey.M, Ctrl: true, Shift: true), "Ctrl+Shift+M"),
            (new PluginKeyChord(PluginKey.D, Alt: true), "Alt+D"), (new PluginKeyChord(PluginKey.M, Alt: true, Shift: true), "Alt+Shift+M") })
        {
            IPluginHotkeyRegistration key = _host.Hotkeys.Register("toggle", "Dungeon map", chord, ToggleWindow);
            if (key.IsBound) { _registrations.Add(key); _keyName = name; break; }
            key.Dispose();
        }
        _host.Log.Info($"map toggle: {_keyName}");
        _lines = _host.WorldLines.CreateLayer();
        MakeCanvas();
    }

    public void Disable()
    {
        _host.Events.Tick -= OnTick;
        _host.Events.LoginComplete -= OnLogin;
        _host.Ui.ThemeChanged -= OnTheme;
        foreach (IDisposable r in _registrations) r.Dispose();
        _registrations.Clear();
        _canvas?.Dispose(); _canvas = null;
        _lines?.Dispose(); _lines = null;
        FlushSettings();
        ReleaseIcons();
        ReleaseImages();
        _mesh = null; _renderingMesh = null;
        _dungeon = null; _landblock = 0; _seenLandblock = uint.MaxValue;
    }

    void OnTheme(PluginUiThemeInfo _) => _canvas?.Invalidate();

    void OnLogin()
    {
        ReleaseIcons();
        EnterLandblock(0);
        _seenLandblock = uint.MaxValue;
    }

    void MakeCanvas()
    {
        _canvas?.Dispose();
        _drag = Drag.None;
        (string _, int w, int h) = Sizes[_settings.Size];
        _canvas = _host.Ui.RegisterCanvas(new PluginCanvasDescriptor("map", w, h)
        {
            Anchor = PluginCanvasAnchor.TopRight,
            Offset = new PluginPoint(_settings.OffsetX, _settings.OffsetY),
            StartVisible = false,
            AcceptsPointerInput = true,
        }, Paint);
        _canvas.PointerHandler = OnPointer;
        _canvas.IsVisible = _dungeon is not null && !_userClosed;
    }

    // ---------------------------------------------------------------- tick

    void OnTick(double _)
    {
        if (_canvas is null) return;
        if (_iconsLoaded && !_host.Ui.Images.IsAvailable)
        {
            ReleaseIcons();
            ReleaseImages();
        }
        FlushSettings();
        if (_drag == Drag.Window) MoveWindow(_dragLatest);
        if (++_tick % 4 == 0) UpdatePlayer();
        if (_linesDirty) UpdateLines();
        Pump3D();
    }

    void MoveWindow(PluginPoint position)
    {
        if (_canvas is null || position == _pressAt) return;
        PluginPoint o = _canvas.Offset;
        _canvas.Offset = new PluginPoint(o.X + position.X - _pressAt.X, o.Y + position.Y - _pressAt.Y);
        _dragLatest = _pressAt;
    }

    void UpdatePlayer()
    {
        PluginNavigationSnapshot snap = _host.Automation.Navigation.Snapshot;
        uint lb = 0;
        if (snap.IsAvailable && !snap.IsPortalSpace && !snap.Position.IsOutdoor)
        {
            uint key = snap.Position.CellId >> 16;
            if (_index.ContainsKey(key)) lb = key;
        }
        if (lb != _seenLandblock) EnterLandblock(lb);
        if (_dungeon is null || _canvas is null) return;

        if (!_iconsLoaded) LoadIcons();
        Vector3 p = PluginDungeonFloorplan.ToLandblockLocal(snap.Position);
        _player = p; _heading = snap.Position.HeadingDegrees;
        int pb = _dungeon.BandAt(p.Z);
        if (pb != _playerBand) { _playerBand = pb; _manualBand = null; _canvas.Invalidate(); }
        if (!Is3D && _canvas.IsVisible && Shown != _loadedBand) LoadBand(Shown);
        if (_follow) _center = new Vector2(p.X, p.Y);

        bool moved = _refreshProgress || Vector3.DistanceSquared(p, _lastPaintedPlayer) > 0.04f || MathF.Abs(_heading - _lastPaintedHeading) > 2f;
        if (moved && _canvas.IsVisible)
        {
            _refreshProgress = false;
            _progress = Current?.Progress(p, _progress) ?? 0;
            _lastPaintedPlayer = p; _lastPaintedHeading = _heading;
            _canvas.Invalidate();
        }
    }

    Route? Current => _dungeon is { Routes.Count: > 0 } d ? d.Routes[Math.Clamp(_route, 0, d.Routes.Count - 1)] : null;

    void EnterLandblock(uint lb)
    {
        _seenLandblock = lb;
        ReleaseImages();
        _progress = 0; _refreshProgress = true;
        _dungeon = null; _landblock = 0; _playerBand = -1; _manualBand = null; _follow = true; _userClosed = false;
        _mesh = null; _renderFailed = false;
        if (lb != 0)
        {
            try
            {
                _dungeon = Dungeon.Load(Path.Combine(_dir, "data", _index[lb].Lb + ".json"));
                _landblock = lb;
                string mesh = Path.Combine(_dir, "data", _index[lb].Lb + ".mesh");
                try { if (File.Exists(mesh)) _mesh = Mesh3D.Load(mesh); }
                catch (Exception ex) { _host.Log.Error($"Could not load the 3D map for {lb:X4}", ex); }
                _route = _settings.Routes.TryGetValue(_dungeon.Lb, out int r) ? Math.Clamp(r, 0, Math.Max(0, _dungeon.Routes.Count - 1)) : 0;
                Say($"Dungeon map: {_dungeon.Title}. {_keyName} or /dmap shows or hides it, /dmap help for more.");
            }
            catch (Exception ex)
            {
                _host.Log.Error($"could not load the map for landblock {lb:X4}", ex);
                _dungeon = null;
            }
        }
        if (_canvas is not null) { _canvas.IsVisible = _dungeon is not null; _canvas.Invalidate(); }
        UpdateLines();
    }

    void LoadIcons()
    {
        if (!_host.Ui.Images.IsAvailable) return;
        foreach (string name in new[] { "player", "goal", "entrance", "exit", "tele", "locked", "lever", "chest", "trap", "mob", "target" })
        {
            string path = Path.Combine(_dir, "art", name + ".png");
            _icons[name] = _host.Ui.Images.FromStream("art/" + name, () => File.OpenRead(path));
        }
        _iconsLoaded = true;
        _canvas?.Invalidate();
    }

    void ReleaseIcons()
    {
        foreach (PluginImage icon in _icons.Values)
            if (icon.IsValid) _host.Ui.Images.Release(icon);
        _icons.Clear();
        _iconsLoaded = false;
    }

    void LoadBand(int band)
    {
        if (_dungeon is null || _dungeon.Bands.Count == 0 || !_host.Ui.Images.IsAvailable) return;
        if (_bandImage.IsValid) _host.Ui.Images.Release(_bandImage);
        string img = _dungeon.Bands[band].Image;
        string path = Path.Combine(_dir, "data", img);
        _bandImage = _host.Ui.Images.FromStream("data/" + img, () => File.OpenRead(path));
        _loadedBand = band;
        _canvas?.Invalidate();
    }

    void ReleaseImages()
    {
        if (_bandImage.IsValid) _host.Ui.Images.Release(_bandImage);
        _loadedBand = -1; _bandImage = PluginImage.None;
        if (_frameImage.IsValid) _host.Ui.Images.Release(_frameImage);
        _frameImage = PluginImage.None; _frame = null; _hasJob = false;
    }

    // ---------------------------------------------------------------- 3D view

    void FallBackToFloorMap()
    {
        if (!_follow) _center = new Vector2(_target3.X, _target3.Y);
        ReleaseImages();
        _renderFailed = true;
        _canvas?.Invalidate();
    }

    /// <summary>Shows a finished render, and starts the next one when the view has changed. One render at a time.</summary>
    void Pump3D()
    {
        bool active = Is3D && _canvas is { IsVisible: true } && _host.Ui.Images.IsAvailable;
        if (_rendering is { IsCompleted: true } done)
        {
            _rendering = null;
            bool current = active && _renderingMesh == _mesh;
            _renderingMesh = null;
            if (done.Exception is { } ex)
            {
                if (current)
                {
                    _host.Log.Error("3D map render failed", ex);
                    FallBackToFloorMap();
                }
                else _hasJob = false;
            }
            else if (current && done.IsCompletedSuccessfully && done.Result.Job.W == _canvas!.Width - 8 &&
                done.Result.Job.H == _canvas.Height - TitleH - FooterH - 2)
            {
                Frame3D f = done.Result;
                PluginImage img = _host.Ui.Images.FromStream($"view3d/{++_frameSerial}", () => new MemoryStream(f.Tga, false));
                if (img.IsValid)
                {
                    if (_frameImage.IsValid) _host.Ui.Images.Release(_frameImage);
                    _frameImage = img; _frame = f;
                    _canvas?.Invalidate();
                }
                else
                {
                    _host.Log.Warn("3D image unavailable, showing the floor map");
                    FallBackToFloorMap();
                }
            }
            else _hasJob = false;
        }
        if (!active || _rendering is not null || _renderFailed || _mesh is null) return;

        RenderJob want = Job(1);
        long now = Environment.TickCount64;
        if (!_hasJob || want != (_lastJob with { Supersample = 1 })) _lastCameraChange = now;
        // Quick renders while the view moves, a smoothed one once it settles.
        want = want with { Supersample = now - _lastCameraChange > 250 ? 2 : 1 };
        if (_hasJob && want == _lastJob) return;
        _lastJob = want; _hasJob = true;
        Mesh3D mesh = _mesh; _renderingMesh = mesh;
        _rendering = Task.Run(() => _renderer.Render(mesh, want));
    }

    RenderJob Job(int supersample)
    {
        Dungeon d = _dungeon!;
        int band = Shown;
        // Levels above the one shown are cut away so it can be seen from above; levels below are dimmed.
        float cut = d.Bands.Count == 0 || band == 0 ? float.MaxValue : d.Bands[band].ZHi;
        float dim = d.Bands.Count == 0 ? float.MinValue : d.Bands[band].ZLo - 0.5f;
        Vector3 t = _follow ? _player : _target3;
        t = new Vector3(MathF.Round(t.X * 10) / 10, MathF.Round(t.Y * 10) / 10, MathF.Round(t.Z * 10) / 10);
        int w = (_canvas?.Width ?? 320) - 8, h = (_canvas?.Height ?? 380) - TitleH - FooterH - 2;
        return new RenderJob(t, _yaw, _pitch, Math.Clamp(_settings.Dist3, 8f, 800f), 1.6f, w, h, cut, dim, supersample);
    }

    // ---------------------------------------------------------------- route on the floor

    void UpdateLines()
    {
        _linesDirty = false;
        if (_lines is null) return;
        Route? r = Current;
        if (_dungeon is null || r is null || !_settings.Lines) { _lines.SetLines([]); return; }
        uint bx = (_landblock >> 8) & 0xFF, by = _landblock & 0xFF;
        PluginNavigationPosition At(Vector3 v) => new((_landblock << 16) | 0x0100u,
            (v.X - 84d + (bx - 127d) * 192d) / 240d, (v.Y - 84d + (by - 127d) * 192d) / 240d, (v.Z + 0.12d) / 240d, 0f, false);
        var lines = new List<PluginWorldLine>(r.Points.Count);
        for (int i = 1; i < r.Points.Count; i++)
        {
            if (r.Breaks[i]) continue;
            lines.Add(new PluginWorldLine(At(r.Points[i - 1]), At(r.Points[i]), LineColour) { WidthMeters = 0.12f });
        }
        _lines.SetLines(lines);
    }

    // ---------------------------------------------------------------- painting

    void Paint(IPluginPainter g)
    {
        int W = g.Width, H = g.Height;
        g.Clear(PluginColor.Transparent);
        g.FillRect(new PluginRect(0, 0, W, H), Ink);
        g.StrokeRect(new PluginRect(0.5, 0.5, W - 1, H - 1), GoldLo, 1);
        g.StrokeRect(new PluginRect(2.5, 2.5, W - 5, H - 5), new PluginColor(201, 162, 90, 70), 1);
        Dungeon? d = _dungeon;
        double lh = g.MeasureText("Ag").Height;

        // Title bar: name, level, level buttons, close.
        g.FillRect(new PluginRect(3, 3, W - 6, TitleH - 3), Leather);
        _rClose = new PluginRect(W - 21, 5, 15, 15);
        DrawX(g, _rClose, GoldHi);
        _rDown = new PluginRect(W - 40, 5, 15, 15);
        _rUp = new PluginRect(W - 57, 5, 15, 15);
        Chevron(g, _rUp, true, d is not null && Shown > 0 ? Gold : GoldLo);
        Chevron(g, _rDown, false, d is not null && Shown < d.Bands.Count - 1 ? Gold : GoldLo);
        string level = d is null || d.Bands.Count == 0 ? "" : $"Level {Shown + 1}/{d.Bands.Count}";
        double lw = g.MeasureText(level).Width;
        g.DrawText(level, new PluginPoint(_rUp.X - 6 - lw, 4 + (16 - lh) / 2), Dim);
        // 3D on or off.
        double bw = g.MeasureText("3D").Width + 8;
        _r3d = new PluginRect(_rUp.X - 12 - lw - bw, 5, bw, 15);
        if (Is3D) g.FillRect(_r3d, new PluginColor(201, 162, 90, 70));
        g.StrokeRect(_r3d, Is3D ? Gold : GoldLo, 1);
        g.DrawText("3D", new PluginPoint(_r3d.X + 4, _r3d.Y + (15 - lh) / 2), Is3D ? GoldHi : Dim);
        _rTitle = new PluginRect(0, 0, _r3d.X - 4, TitleH);
        g.DrawText(Fit(g, d?.Title ?? "Dungeon map", _rTitle.Width - 12), new PluginPoint(8, 4 + (16 - lh) / 2), GoldHi);
        g.DrawLine(new PluginPoint(3, TitleH), new PluginPoint(W - 3, TitleH), GoldLo);

        // Map.
        _rMap = new PluginRect(4, TitleH + 1, W - 8, H - TitleH - FooterH - 2);
        g.PushClip(_rMap);
        g.FillRect(_rMap, new PluginColor(8, 6, 4, 255));
        if (d is not null)
        {
            if (Is3D) PaintMap3D(g, d);
            else PaintMap(g, d);
            if (!_follow)
            {
                const string hint = "Right-click to follow";
                g.DrawText(hint, new PluginPoint(_rMap.X + 6, _rMap.Y + _rMap.Height - g.MeasureText(hint).Height - 4), Dim, outline: true);
            }
        }
        g.PopClip();
        g.DrawLine(new PluginPoint(3, _rMap.Y + _rMap.Height), new PluginPoint(W - 3, _rMap.Y + _rMap.Height), GoldLo);

        // Footer: route picker, the step you are on, distance left.
        double fy = H - FooterH + 1;
        g.FillRect(new PluginRect(3, fy, W - 6, FooterH - 4), Leather);
        Route? r = Current;
        _rPrev = new PluginRect(6, fy + 2, 18, 18);
        _rNext = new PluginRect(W - 24, fy + 2, 18, 18);
        bool many = d is { Routes.Count: > 1 };
        Arrow(g, _rPrev, false, many ? GoldHi : GoldLo);
        Arrow(g, _rNext, true, many ? GoldHi : GoldLo);
        string name = r is null ? "No route" : $"{r.Name}, {r.Length} m";
        if (d is { Routes.Count: > 1 }) name = $"{_route + 1}/{d.Routes.Count}  {name}";
        name = Fit(g, name, W - 60);
        g.DrawText(name, new PluginPoint((W - g.MeasureText(name).Width) / 2, fy + 2 + (18 - lh) / 2), Gold);
        if (r is { Steps.Count: > 0 })
        {
            int si = 0;
            for (int i = 0; i < r.Steps.Count; i++) if (r.Steps[i].Start <= _progress + 1) si = i;
            string step = r.Steps[si].Text;
            List<string> lines = Wrap(g, step, W - 16);
            for (int i = 0; i < Math.Min(2, lines.Count); i++)
                g.DrawText(lines[i], new PluginPoint(8, fy + 22 + i * (lh + 1)), Text);
            string left = $"Step {si + 1} of {r.Steps.Count}, {Math.Max(0, r.Length - (int)_progress)} m to go";
            if (lines.Count < 2) g.DrawText(left, new PluginPoint(8, fy + 22 + (lh + 1)), Dim);
        }
    }

    int Shown => _manualBand ?? Math.Max(0, _playerBand);

    bool OutsideMap(PluginPoint a, PluginPoint b) =>
        Math.Max(a.X, b.X) < _rMap.X - 2 || Math.Min(a.X, b.X) > _rMap.X + _rMap.Width + 2 ||
        Math.Max(a.Y, b.Y) < _rMap.Y - 2 || Math.Min(a.Y, b.Y) > _rMap.Y + _rMap.Height + 2;

    void PaintMap(IPluginPainter g, Dungeon d)
    {
        float z = _settings.Zoom;
        double mx = _rMap.X + _rMap.Width / 2, my = _rMap.Y + _rMap.Height / 2;
        PluginPoint S(Vector3 v) => new(mx + (v.X - _center.X) * z, my - (v.Y - _center.Y) * z);
        bool InView(PluginPoint p, double pad = 12) =>
            p.X > _rMap.X - pad && p.X < _rMap.X + _rMap.Width + pad && p.Y > _rMap.Y - pad && p.Y < _rMap.Y + _rMap.Height + pad;

        if (_loadedBand == Shown && _bandImage.IsValid)
        {
            PluginPoint tl = S(new Vector3(d.X0, d.Y1, 0));
            g.DrawImage(_bandImage, new PluginRect(tl.X, tl.Y, _bandImage.Width / d.Scale * z, _bandImage.Height / d.Scale * z), PluginColor.White);
        }
        int band = Shown;
        bool OnBand(float zz) => d.Bands.Count == 0 || d.BandAt(zz) == band;

        // Monsters on this level: targets bright, the rest small.
        if (_icons.TryGetValue("mob", out PluginImage mob) && _icons.TryGetValue("target", out PluginImage tgt))
            foreach ((Vector3 p, bool target) in d.Spawns)
            {
                if (!OnBand(p.Z)) continue;
                PluginPoint s = S(p); if (!InView(s)) continue;
                PluginImage im = target ? tgt : mob;
                if (im.IsValid) g.DrawImage(im, new PluginRect(s.X - im.Width / 2.0, s.Y - im.Height / 2.0, im.Width, im.Height), new PluginColor(255, 255, 255, 200));
            }

        // Route: ahead bright, behind dim, other levels faint.
        if (Current is { } r)
            for (int i = 1; i < r.Points.Count; i++)
            {
                if (r.Breaks[i]) continue;
                Vector3 a = r.Points[i - 1], b = r.Points[i];
                PluginPoint sa = S(a), sb = S(b);
                if (OutsideMap(sa, sb)) continue;
                bool on = OnBand((a.Z + b.Z) / 2);
                if (!on) g.DrawLine(sa, sb, RouteOff, 1.5f);
                else if (r.Along[i] <= _progress) g.DrawLine(sa, sb, RouteBehind, 2f);
                else g.DrawLine(sa, sb, RouteAhead, 3f);
            }

        // Markers: goals, entrances, portals, locked doors, levers, chests, traps.
        var labels = new List<PluginRect>();
        foreach (Marker m in d.Markers)
        {
            if (!_icons.TryGetValue(m.Kind, out PluginImage im) || !im.IsValid) continue;
            PluginPoint s = S(m.P); if (!InView(s)) continue;
            bool on = OnBand(m.P.Z);
            g.DrawImage(im, new PluginRect(s.X - im.Width / 2.0, s.Y - im.Height / 2.0, im.Width, im.Height), on ? PluginColor.White : new PluginColor(255, 255, 255, 80));
            if (on && m.Kind is "goal" or "entrance")
            {
                DrawMarkerLabel(g, m, im, s, labels);
            }
        }

        // The character: an arrow turned to its heading, dimmed when another level is shown.
        if (_icons.TryGetValue("player", out PluginImage arrow) && arrow.IsValid)
        {
            PluginPoint s = S(_player);
            bool here = _playerBand == band;
            g.DrawImageTransformed(arrow, new PluginRect(s.X - arrow.Width / 2.0, s.Y - arrow.Height / 2.0, arrow.Width, arrow.Height),
                here ? PluginColor.White : new PluginColor(255, 255, 255, 90), _heading * Math.PI / 180, new PluginPoint(arrow.Width / 2.0, arrow.Height / 2.0));
        }

    }

    void PaintMap3D(IPluginPainter g, Dungeon d)
    {
        Frame3D? f = _frame;
        if (f is null || !_frameImage.IsValid || f.Cam.W != _rMap.Width || f.Cam.H != _rMap.Height)
        {
            const string wait = "Drawing the 3D map...";
            PluginSize sz = g.MeasureText(wait);
            g.DrawText(wait, new PluginPoint(_rMap.X + (_rMap.Width - sz.Width) / 2, _rMap.Y + (_rMap.Height - sz.Height) / 2), Dim);
            return;
        }
        Camera3D cam = f.Cam;
        double ox = _rMap.X, oy = _rMap.Y;
        g.DrawImage(_frameImage, new PluginRect(ox, oy, cam.W, cam.H), PluginColor.White);
        bool Cut(float z) => z > f.Job.CutZ + 0.5f;
        bool At(Vector3 p, out PluginPoint s, out bool seen)
        {
            seen = false; s = default;
            if (!cam.Project(p, out float sx, out float sy, out float depth)) return false;
            if (sx < -40 || sy < -40 || sx > cam.W + 40 || sy > cam.H + 40) return false;
            s = new PluginPoint(ox + sx, oy + sy);
            seen = f.Visible(sx, sy, depth);
            return true;
        }
        var lift = new Vector3(0, 0, 0.3f);

        // Monsters, hidden behind walls.
        if (_icons.TryGetValue("mob", out PluginImage mob) && _icons.TryGetValue("target", out PluginImage tgt))
            foreach ((Vector3 p, bool target) in d.Spawns)
            {
                if (Cut(p.Z) || !At(p + new Vector3(0, 0, 0.8f), out PluginPoint s, out bool seen) || !seen) continue;
                PluginImage im = target ? tgt : mob;
                if (im.IsValid) g.DrawImage(im, new PluginRect(s.X - im.Width / 2.0, s.Y - im.Height / 2.0, im.Width, im.Height), new PluginColor(255, 255, 255, 200));
            }

        // Route: ahead bright, behind dim, faint where a wall or floor is in the way.
        if (Current is { } r)
            for (int i = 1; i < r.Points.Count; i++)
            {
                if (r.Breaks[i]) continue;
                Vector3 a = r.Points[i - 1] + lift, b = r.Points[i] + lift;
                if (!cam.ProjectSegment(a, b, f.Job.CutZ + 0.5f, out Vector3 start, out Vector3 end)) continue;
                var sa = new PluginPoint(ox + start.X, oy + start.Y);
                var sb = new PluginPoint(ox + end.X, oy + end.Y);
                if (OutsideMap(sa, sb)) continue;
                float depth = 2 / (1 / start.Z + 1 / end.Z);
                if (!f.Visible((start.X + end.X) / 2, (start.Y + end.Y) / 2, depth, 1.2f)) g.DrawLine(sa, sb, RouteOff, 1.5f);
                else if (r.Along[i] <= _progress) g.DrawLine(sa, sb, RouteBehind, 2f);
                else g.DrawLine(sa, sb, RouteAhead, 3f);
            }

        // Markers, dimmed behind walls, with goal and entrance names.
        var labels = new List<PluginRect>();
        foreach (Marker m in d.Markers)
        {
            if (!_icons.TryGetValue(m.Kind, out PluginImage im) || !im.IsValid || Cut(m.P.Z)) continue;
            if (!At(m.P + new Vector3(0, 0, 1f), out PluginPoint s, out bool seen)) continue;
            g.DrawImage(im, new PluginRect(s.X - im.Width / 2.0, s.Y - im.Height / 2.0, im.Width, im.Height), seen ? PluginColor.White : new PluginColor(255, 255, 255, 90));
            if (seen && m.Kind is "goal" or "entrance")
            {
                DrawMarkerLabel(g, m, im, s, labels);
            }
        }

        // The character: an arrow turned the way it faces on screen.
        if (_icons.TryGetValue("player", out PluginImage arrow) && arrow.IsValid && At(_player + lift, out PluginPoint ps, out _))
        {
            float hr = _heading * MathF.PI / 180;
            double turn = _heading * Math.PI / 180;
            if (cam.Project(_player + lift + new Vector3(MathF.Sin(hr), MathF.Cos(hr), 0) * 2, out float hx, out float hy, out _))
                turn = Math.Atan2(ox + hx - ps.X, -(oy + hy - ps.Y));
            g.DrawImageTransformed(arrow, new PluginRect(ps.X - arrow.Width / 2.0, ps.Y - arrow.Height / 2.0, arrow.Width, arrow.Height),
                _playerBand == Shown ? PluginColor.White : new PluginColor(255, 255, 255, 110), turn, new PluginPoint(arrow.Width / 2.0, arrow.Height / 2.0));
        }

    }

    static void DrawMarkerLabel(IPluginPainter g, Marker marker, PluginImage icon, PluginPoint p, List<PluginRect> labels)
    {
        PluginSize size = g.MeasureText(marker.Name);
        var at = new PluginRect(p.X + icon.Width / 2.0 + 3, p.Y - size.Height / 2, size.Width, size.Height);
        for (int i = 0; i < labels.Count; i++)
        {
            var other = labels[i];
            if (other.X < at.X + at.Width && at.X < other.X + other.Width &&
                other.Y < at.Y + at.Height && at.Y < other.Y + other.Height)
            {
                at = at with { Y = at.Y + size.Height + 1 };
                i = -1;
            }
        }
        labels.Add(at);
        g.DrawText(marker.Name, new PluginPoint(at.X, at.Y), marker.Kind == "goal" ? GoldHi : Text, outline: true);
    }

    static void DrawX(IPluginPainter g, PluginRect r, PluginColor c)
    {
        g.DrawLine(new PluginPoint(r.X + 3, r.Y + 3), new PluginPoint(r.X + r.Width - 3, r.Y + r.Height - 3), c, 2);
        g.DrawLine(new PluginPoint(r.X + r.Width - 3, r.Y + 3), new PluginPoint(r.X + 3, r.Y + r.Height - 3), c, 2);
    }

    static void Chevron(IPluginPainter g, PluginRect r, bool up, PluginColor c)
    {
        double cx = r.X + r.Width / 2, top = r.Y + 4, bottom = r.Y + r.Height - 4;
        PluginPoint tip = new(cx, up ? top : bottom), l = new(r.X + 2, up ? bottom : top), rr = new(r.X + r.Width - 2, up ? bottom : top);
        g.DrawLine(l, tip, c, 2); g.DrawLine(tip, rr, c, 2);
    }

    static void Arrow(IPluginPainter g, PluginRect r, bool right, PluginColor c)
    {
        double cy = r.Y + r.Height / 2, a = r.X + 5, b = r.X + r.Width - 5;
        PluginPoint tip = new(right ? b : a, cy), t = new(right ? a : b, r.Y + 3), bt = new(right ? a : b, r.Y + r.Height - 3);
        g.DrawLine(t, tip, c, 2); g.DrawLine(tip, bt, c, 2);
    }

    static string Fit(IPluginPainter g, string s, double width)
    {
        if (g.MeasureText(s).Width <= width) return s;
        const string dots = "...";
        if (g.MeasureText(dots).Width > width) return "";
        int low = 0, high = s.Length;
        while (low < high)
        {
            int mid = low + (high - low + 1) / 2;
            if (g.MeasureText(s[..mid] + dots).Width <= width) low = mid;
            else high = mid - 1;
        }
        if (low > 0 && char.IsHighSurrogate(s[low - 1])) low--;
        return s[..low].TrimEnd() + dots;
    }

    static List<string> Wrap(IPluginPainter g, string s, double width)
    {
        var lines = new List<string>(); string cur = "";
        foreach (string w in s.Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            string next = cur.Length == 0 ? w : cur + " " + w;
            if (cur.Length > 0 && g.MeasureText(next).Width > width)
            {
                lines.Add(cur);
                if (lines.Count == 2) return lines;
                cur = w;
            }
            else cur = next;
        }
        if (cur.Length > 0) lines.Add(cur);
        return lines;
    }

    // ---------------------------------------------------------------- input

    static bool In(PluginRect r, PluginPoint p) => p.X >= r.X && p.X < r.X + r.Width && p.Y >= r.Y && p.Y < r.Y + r.Height;

    void OnPointer(PluginPointerEvent e)
    {
        if (_canvas is null) return;
        switch (e.Kind)
        {
            case PluginPointerEventKind.Down when e.Button == PluginPointerButton.Right:
                if (In(_rMap, e.Position)) { _drag = Drag.Right; _rightMoved = false; _pressAt = e.Position; }
                break;
            case PluginPointerEventKind.Down when e.Button == PluginPointerButton.Left:
                if (In(_rClose, e.Position)) { _userClosed = true; _canvas.IsVisible = false; }
                else if (In(_rPrev, e.Position)) StepRoute(-1);
                else if (In(_rNext, e.Position)) StepRoute(1);
                else if (In(_rUp, e.Position)) StepLevel(-1);
                else if (In(_rDown, e.Position)) StepLevel(1);
                else if (In(_r3d, e.Position) && _mesh is not null) SetView3D(!Is3D);
                else if (In(_rTitle, e.Position)) { _drag = Drag.Window; _pressAt = _dragLatest = e.Position; }
                else if (In(_rMap, e.Position)) { _drag = Is3D ? Drag.Orbit : Drag.Pan; _pressAt = e.Position; }
                break;
            case PluginPointerEventKind.Move when _drag == Drag.Window:
                _dragLatest = e.Position;
                break;
            case PluginPointerEventKind.Move when _drag == Drag.Pan:
                if (_follow && Math.Abs(e.Position.X - _pressAt.X) + Math.Abs(e.Position.Y - _pressAt.Y) < 4) break;
                _follow = false;
                _center += new Vector2((float)(-(e.Position.X - _pressAt.X) / _settings.Zoom), (float)((e.Position.Y - _pressAt.Y) / _settings.Zoom));
                _pressAt = e.Position;
                _canvas.Invalidate();
                break;
            case PluginPointerEventKind.Move when _drag == Drag.Orbit:
                // Drag sideways to turn, up and down to tilt.
                _yaw += (float)(e.Position.X - _pressAt.X) * 0.008f;
                _pitch = Math.Clamp(_pitch + (float)(e.Position.Y - _pressAt.Y) * 0.006f, 0.12f, 1.5f);
                _pressAt = e.Position;
                break;
            case PluginPointerEventKind.Move when _drag == Drag.Right:
                if (!_rightMoved && Math.Abs(e.Position.X - _pressAt.X) + Math.Abs(e.Position.Y - _pressAt.Y) < 4) break;
                _rightMoved = true;
                if (Is3D)
                {
                    // Right-drag slides the view over the ground.
                    if (_follow) { _target3 = _player; _follow = false; }
                    float mpp = _settings.Dist3 / ((_canvas.Height - TitleH - FooterH - 2) / 2f / MathF.Tan(22.5f * MathF.PI / 180f));
                    var right = new Vector2(MathF.Cos(_yaw), -MathF.Sin(_yaw));
                    var forward = new Vector2(MathF.Sin(_yaw), MathF.Cos(_yaw));
                    Vector2 mv = (-right * (float)(e.Position.X - _pressAt.X) +
                        forward * (float)(e.Position.Y - _pressAt.Y) / MathF.Sin(_pitch)) * mpp;
                    _target3 += new Vector3(mv, 0);
                    _canvas.Invalidate();
                }
                _pressAt = e.Position;
                break;
            case PluginPointerEventKind.Up when _drag == Drag.Right:
                if (!_rightMoved) { _follow = true; _manualBand = null; _center = new Vector2(_player.X, _player.Y); _canvas.Invalidate(); }
                _drag = Drag.None;
                break;
            case PluginPointerEventKind.Up or PluginPointerEventKind.Cancelled:
                if (_drag == Drag.Window)
                {
                    MoveWindow(e.Kind == PluginPointerEventKind.Up ? e.Position : _dragLatest);
                    _settings.OffsetX = _canvas.Offset.X;
                    _settings.OffsetY = _canvas.Offset.Y;
                    QueueSettingsSave();
                }
                _drag = Drag.None;
                break;
            case PluginPointerEventKind.Wheel when e.WheelDelta != 0:
                if ((e.Modifiers & PluginKeyModifiers.Shift) != 0) { StepLevel(e.WheelDelta > 0 ? -1 : 1); break; }
                if (Is3D) { _settings.Dist3 = Math.Clamp(_settings.Dist3 * MathF.Pow(1.2f, -e.WheelDelta), 8f, 800f); QueueSettingsSave(); break; }
                float before = _settings.Zoom;
                _settings.Zoom = Math.Clamp(before * MathF.Pow(1.25f, e.WheelDelta), 0.5f, 10f);
                if (!_follow && In(_rMap, e.Position))
                {
                    // Keep the point under the pointer still.
                    double mx = _rMap.X + _rMap.Width / 2, my = _rMap.Y + _rMap.Height / 2;
                    var at = new Vector2((float)((e.Position.X - mx) / before), (float)(-(e.Position.Y - my) / before));
                    _center += at - at * (before / _settings.Zoom);
                }
                QueueSettingsSave();
                _canvas.Invalidate();
                break;
        }
    }

    bool Is3D => _settings.View3D && _mesh is not null && !_renderFailed;

    void SetView3D(bool on)
    {
        if (_mesh is not null && on != Is3D && !_follow)
        {
            if (on) _target3 = new Vector3(_center, _player.Z);
            else _center = new Vector2(_target3.X, _target3.Y);
        }
        _settings.View3D = on;
        if (on) _renderFailed = false;
        QueueSettingsSave();
        _canvas?.Invalidate();
    }

    void StepRoute(int by)
    {
        if (_dungeon is not { Routes.Count: > 1 } d) return;
        _route = (_route + by + d.Routes.Count) % d.Routes.Count;
        _settings.Routes[d.Lb] = _route; QueueSettingsSave();
        _progress = Current?.Progress(_player) ?? 0;
        _linesDirty = true;
        _canvas?.Invalidate();
    }

    void StepLevel(int by)
    {
        if (_dungeon is not { Bands.Count: > 0 } d) return;
        _manualBand = Math.Clamp(Shown + by, 0, d.Bands.Count - 1);
        _canvas?.Invalidate();
    }

    void ToggleWindow()
    {
        if (_canvas is null) return;
        if (_dungeon is null) { Say("Dungeon map: you are not in a mapped dungeon. /dmap list shows them."); return; }
        _userClosed = _canvas.IsVisible;
        _canvas.IsVisible = !_canvas.IsVisible;
        if (_canvas.IsVisible) { _refreshProgress = true; _canvas.Invalidate(); }
    }

    // ---------------------------------------------------------------- chat command

    void OnCommand(PluginCommand c)
    {
        IReadOnlyList<string> a;
        try { a = c.ParseArguments(); }
        catch (FormatException) { Say("Close the quote in the command."); return; }
        string verb = a.Count > 0 ? a[0].ToLowerInvariant() : "";
        string rest = a.Count > 1 ? string.Join(' ', a.Skip(1)) : "";
        switch (verb)
        {
            case "":
                ToggleWindow(); break;
            case "route":
                if (_dungeon is null) { Say("Not in a mapped dungeon."); break; }
                if (int.TryParse(rest, out int n) && n >= 1 && n <= _dungeon.Routes.Count) StepRoute(n - 1 - _route);
                else for (int i = 0; i < _dungeon.Routes.Count; i++) Say($"{i + 1}. {_dungeon.Routes[i].Name} ({_dungeon.Routes[i].Length} m){(i == _route ? "  [shown]" : "")}");
                break;
            case "next": StepRoute(1); break;
            case "3d": SetView3D(true); break;
            case "2d": SetView3D(false); break;
            case "lines":
                _settings.Lines = rest.ToLowerInvariant() switch { "on" => true, "off" => false, _ => !_settings.Lines };
                QueueSettingsSave(); UpdateLines();
                Say($"Route on the floor: {(_settings.Lines ? "on" : "off")}.");
                break;
            case "size":
                int si = Array.FindIndex(Sizes, s => s.Name == rest.ToLowerInvariant());
                if (si < 0) { Say("Sizes: small, medium, large."); break; }
                _settings.Size = si; QueueSettingsSave(); MakeCanvas(); _canvas!.Invalidate();
                break;
            case "reset":
                _settings.OffsetX = -12; _settings.OffsetY = 160;
                _settings.Zoom = 2f; _settings.Dist3 = 55f;
                QueueSettingsSave(); MakeCanvas(); _canvas!.Invalidate();
                break;
            case "info":
                if (_dungeon is null) { Say("Not in a mapped dungeon."); break; }
                Say($"{_dungeon.Title}: {_dungeon.Subtitle}");
                for (int i = 0; i < _dungeon.Steps.Count; i++) Say($"{i + 1}. {_dungeon.Steps[i]}");
                foreach ((string title, string body) in _dungeon.Notes) Say($"{title}: {body}");
                break;
            case "list":
                foreach (IGrouping<string, IndexEntry> g in _index.Values.GroupBy(e => e.Group).OrderBy(g => g.Key))
                    Say($"{g.Key}: {string.Join(", ", g.OrderBy(e => e.Title).Select(e => e.Title))}");
                break;
            case "goto":
                GoTo(rest); break;
            case "tp":
                if (_dungeon is null) { Say("Not in a mapped dungeon."); break; }
                Marker? mk = _dungeon.Markers.FirstOrDefault(x => x.Cell != 0 && x.Name.Contains(rest.Trim(), StringComparison.OrdinalIgnoreCase));
                if (rest.Trim().Length == 0 || mk is null) { Say("Markers: " + string.Join(", ", _dungeon.Markers.Where(x => x.Cell != 0).Select(x => x.Name).Distinct())); break; }
                Teleport(mk, mk.Name);
                break;
            default:
                Say($"/dmap: show or hide the map ({_keyName}). /dmap route [n], /dmap next, /dmap 3d or 2d, /dmap info, /dmap lines [on|off], /dmap size small|medium|large, /dmap reset, /dmap list. Admins on a test server: /dmap goto <dungeon>, /dmap tp <marker>.");
                Say("On the 3D map: drag to turn and tilt, right-drag to slide, wheel to zoom. On the 2D map: drag to pan, wheel to zoom. Both: Shift+wheel or the arrows to change level, right-click to follow you again.");
                break;
        }
    }

    /// <summary>Admin shortcut for the local test server: teleports to a mapped dungeon's entrance with ACE's @teleloc.</summary>
    void GoTo(string query)
    {
        string q = query.Trim().ToLowerInvariant();
        IndexEntry? e = _index.Values.FirstOrDefault(x => x.Title.Equals(q, StringComparison.OrdinalIgnoreCase) || x.Slug == q)
            ?? _index.Values.FirstOrDefault(x => x.Title.Contains(q, StringComparison.OrdinalIgnoreCase) || x.Slug.Contains(q));
        if (q.Length == 0 || e is null) { Say("No mapped dungeon matches that. /dmap list shows them."); return; }
        Dungeon d;
        try { d = Dungeon.Load(Path.Combine(_dir, "data", e.Lb + ".json")); }
        catch (Exception ex)
        {
            _host.Log.Error($"Could not load the map for {e.Lb}", ex);
            Say($"Could not load {e.Title}.");
            return;
        }
        Marker? m = d.Markers.FirstOrDefault(x => x.Kind == "entrance" && x.Cell != 0);
        if (m is null) { Say($"{d.Title} has no entrance on record."); return; }
        Teleport(m, d.Title);
    }

    void Teleport(Marker m, string what)
    {
        string cmd = FormattableString.Invariant($"@teleloc 0x{m.Cell:X8} [{m.P.X:0.00} {m.P.Y:0.00} {m.P.Z + 0.05f:0.00}] 1 0 0 0");
        Say($"Going to {what}: {cmd}");
        _host.Automation.Chat.Submit(cmd);
    }

    void Say(string text) => _host.Automation.Chat.PostSystemMessage(text);

    // ---------------------------------------------------------------- settings

    void LoadSettings()
    {
        try
        {
            string? json = _host.Storage.IsAvailable ? _host.Storage.ReadText("settings.json") : null;
            if (json is null) return;
            using var doc = JsonDocument.Parse(json);
            JsonElement r = doc.RootElement;
            var loaded = new Settings();
            if (r.TryGetProperty("OffsetX", out JsonElement v)) loaded.OffsetX = v.GetDouble();
            if (r.TryGetProperty("OffsetY", out v)) loaded.OffsetY = v.GetDouble();
            if (r.TryGetProperty("Size", out v)) loaded.Size = v.GetInt32();
            if (r.TryGetProperty("Lines", out v)) loaded.Lines = v.GetBoolean();
            if (r.TryGetProperty("Zoom", out v)) loaded.Zoom = v.GetSingle();
            if (r.TryGetProperty("View3D", out v)) loaded.View3D = v.GetBoolean();
            if (r.TryGetProperty("Dist3", out v)) loaded.Dist3 = v.GetSingle();
            if (r.TryGetProperty("Routes", out v))
                foreach (JsonProperty p in v.EnumerateObject()) loaded.Routes[p.Name] = p.Value.GetInt32();
            loaded.Size = Math.Clamp(loaded.Size, 0, Sizes.Length - 1);
            if (!double.IsFinite(loaded.OffsetX) || !double.IsFinite(loaded.OffsetY) ||
                !float.IsFinite(loaded.Zoom) || !float.IsFinite(loaded.Dist3))
                throw new InvalidDataException("Invalid window or zoom setting");
            loaded.Zoom = Math.Clamp(loaded.Zoom, 0.5f, 10f);
            loaded.Dist3 = Math.Clamp(loaded.Dist3, 8f, 800f);
            _settings = loaded;
        }
        catch (Exception ex) { _host.Log.Warn("settings unreadable, using defaults: " + ex.Message); }
    }

    void QueueSettingsSave() => _settingsDirty = true;

    void FlushSettings()
    {
        if (!_settingsDirty || !_host.Storage.IsAvailable) return;
        _settingsDirty = false;
        try
        {
            // Keep plugin types out of the serializer cache so the host can unload them.
            var routes = new JsonObject();
            foreach (var (lb, route) in _settings.Routes) routes[lb] = route;
            var json = new JsonObject
            {
                ["OffsetX"] = _settings.OffsetX, ["OffsetY"] = _settings.OffsetY,
                ["Size"] = _settings.Size, ["Lines"] = _settings.Lines, ["Zoom"] = _settings.Zoom,
                ["View3D"] = _settings.View3D, ["Dist3"] = _settings.Dist3, ["Routes"] = routes,
            };
            _host.Storage.WriteText("settings.json", json.ToJsonString());
        }
        catch (Exception ex) { _host.Log.Warn("Could not save settings: " + ex.Message); }
    }
}
