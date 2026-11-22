using System.Numerics;
using System.Text.Json;

namespace AcDungeonMaps;

/// <summary>One mapped dungeon, read from data/&lt;LB&gt;.json. Positions are landblock-local metres (x east, y north, z up).</summary>
internal sealed class Dungeon
{
    public required string Lb { get; init; }
    public required string Title { get; init; }
    public string Subtitle { get; init; } = "";
    public float X0, Y1, Scale;
    public List<Band> Bands { get; } = [];
    public List<Route> Routes { get; } = [];
    public List<Marker> Markers { get; } = [];
    public List<(Vector3 P, bool Target)> Spawns { get; } = [];
    public List<string> Steps { get; } = [];
    public List<(string Title, string Body)> Notes { get; } = [];

    public int BandAt(float z)
    {
        for (int i = 0; i < Bands.Count; i++)
            if (z >= Bands[i].ZLo && z < Bands[i].ZHi) return i;
        int best = 0; float bestD = float.MaxValue;
        for (int i = 0; i < Bands.Count; i++)
        {
            float d = MathF.Min(MathF.Abs(z - Bands[i].ZLo), MathF.Abs(z - Bands[i].ZHi));
            if (d < bestD) { bestD = d; best = i; }
        }
        return best;
    }

    public static Dungeon Load(string path)
    {
        using var file = File.OpenRead(path);
        using var doc = JsonDocument.Parse(file);
        JsonElement r = doc.RootElement;
        JsonElement b = r.GetProperty("bounds");
        var d = new Dungeon
        {
            Lb = r.GetProperty("lb").GetString()!,
            Title = r.GetProperty("title").GetString() ?? throw new InvalidDataException("Missing dungeon title"),
            Subtitle = r.GetProperty("subtitle").GetString() ?? "",
            X0 = Number(b.GetProperty("x0")), Y1 = Number(b.GetProperty("y1")),
            Scale = Number(b.GetProperty("scale")),
        };
        if (d.Scale <= 0 || !string.Equals(d.Lb, Path.GetFileNameWithoutExtension(path), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Invalid map scale or landblock");
        foreach (JsonElement e in r.GetProperty("bands").EnumerateArray())
        {
            string? img = e.GetProperty("img").GetString();
            if (string.IsNullOrWhiteSpace(img) || img.IndexOfAny(['/', '\\', ':']) >= 0 ||
                !img.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Invalid floor image name");
            d.Bands.Add(new Band(Number(e.GetProperty("zlo")), Number(e.GetProperty("zhi")), img));
        }
        foreach (JsonElement e in r.GetProperty("routes").EnumerateArray())
            d.Routes.Add(Route.Read(e));
        foreach (JsonElement e in r.GetProperty("markers").EnumerateArray())
            d.Markers.Add(new Marker(e.GetProperty("k").GetString() ?? "", e.GetProperty("n").GetString() ?? "", V(e.GetProperty("p")),
                e.TryGetProperty("c", out JsonElement c) ? Convert.ToUInt32(c.GetString(), 16) : 0u));
        foreach (JsonElement e in r.GetProperty("spawns").EnumerateArray())
            d.Spawns.Add((V(e), e[3].GetInt32() != 0));
        foreach (JsonElement e in r.GetProperty("steps").EnumerateArray())
            d.Steps.Add(e.GetString() ?? "");
        foreach (JsonElement e in r.GetProperty("notes").EnumerateArray())
            d.Notes.Add((e.GetProperty("title").GetString() ?? "", e.GetProperty("body").GetString() ?? ""));
        return d;
    }

    internal static float Number(JsonElement e)
    {
        float value = e.GetSingle();
        return float.IsFinite(value) ? value : throw new InvalidDataException("Invalid map coordinate");
    }

    internal static Vector3 V(JsonElement a) => new(Number(a[0]), Number(a[1]), Number(a[2]));
}

internal sealed record Band(float ZLo, float ZHi, string Image);

internal sealed record Marker(string Kind, string Name, Vector3 P, uint Cell);

internal sealed record RouteStep(float Start, string Text);

/// <summary>A route as one polyline. <see cref="Breaks"/>[i] is true where point i starts a new piece (after a portal).</summary>
internal sealed class Route
{
    public required string Name { get; init; }
    public int Length { get; init; }
    public List<Vector3> Points { get; } = [];
    public List<bool> Breaks { get; } = [];
    public List<float> Along { get; } = [];
    public List<RouteStep> Steps { get; } = [];

    public static Route Read(JsonElement e)
    {
        var r = new Route { Name = e.GetProperty("name").GetString() ?? "", Length = e.GetProperty("len").GetInt32() };
        float along = 0;
        foreach (JsonElement leg in e.GetProperty("legs").EnumerateArray())
        {
            bool brk = true;
            foreach (JsonElement p in leg.EnumerateArray())
            {
                if (p.ValueKind == JsonValueKind.Null) { brk = true; continue; }
                Vector3 v = Dungeon.V(p);
                if (!brk && r.Points.Count > 0) along += Vector3.Distance(r.Points[^1], v);
                r.Points.Add(v); r.Breaks.Add(brk); r.Along.Add(along);
                brk = false;
            }
        }
        foreach (JsonElement s in e.GetProperty("steps").EnumerateArray())
            r.Steps.Add(new RouteStep(Dungeon.Number(s.GetProperty("s")), s.GetProperty("t").GetString() ?? ""));
        return r;
    }

    /// <summary>
    /// How far along the route the character at <paramref name="p"/> is, in metres. Height counts triple so a level
    /// above or below doesn't match. A route that passes the same place twice (a sweep) is read forwards: the
    /// stretch just behind and ahead of <paramref name="last"/> wins while the character is near it.
    /// </summary>
    public float Progress(Vector3 p, float last = -1)
    {
        if (last >= 0 && Nearest(p, last - 25, last + 120) is (float d, float at) && d < 15 * 15) return at;
        return Nearest(p, float.MinValue, float.MaxValue)?.At ?? 0;
    }

    (float Dist, float At)? Nearest(Vector3 p, float from, float to)
    {
        float best = float.MaxValue, at = 0; bool any = false;
        for (int i = 0; i < Points.Count; i++)
        {
            float start = Breaks[i] ? Along[i] : Along[i - 1];
            if (Along[i] < from || start > to) continue;
            Vector3 d = Points[i] - p;
            d.Z *= 3;
            float along = Along[i];
            if (!Breaks[i] && along > start)
            {
                Vector3 edge = Points[i] - Points[i - 1];
                edge.Z *= 3;
                float length = along - start;
                float back = Math.Clamp(Vector3.Dot(d, edge) / edge.LengthSquared(),
                    MathF.Max(0, (along - to) / length), MathF.Min(1, (along - from) / length));
                d -= back * edge;
                along -= back * length;
            }
            float dd = d.LengthSquared();
            if (dd < best) { best = dd; at = along; any = true; }
        }
        return any ? (best, at) : null;
    }
}

internal sealed record IndexEntry(string Lb, string Slug, string Title, string Group);
