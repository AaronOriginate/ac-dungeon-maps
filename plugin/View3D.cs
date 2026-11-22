using System.Numerics;

namespace AcDungeonMaps;

/// <summary>
/// A dungeon's floors and walls, read from data/&lt;LB&gt;.mesh, in landblock-local metres (x east, y north, z up).
/// The first <see cref="FloorTris"/> triangles are floors, the rest walls.
/// </summary>
internal sealed class Mesh3D
{
    public required Vector3[] Vertices { get; init; }
    public int FloorTris { get; init; }
    public int Tris => Vertices.Length / 3;
    public float ZMin { get; init; }
    public float ZMax { get; init; }

    public static Mesh3D Load(string path)
    {
        using var r = new BinaryReader(File.OpenRead(path));
        if (!r.ReadBytes(4).AsSpan().SequenceEqual("DMS1"u8)) throw new InvalidDataException("Invalid dungeon mesh");
        float cx = r.ReadSingle(), cy = r.ReadSingle();
        int floors = r.ReadInt32(), walls = r.ReadInt32();
        long triangles = (long)floors + walls;
        if (!float.IsFinite(cx) || !float.IsFinite(cy) || floors < 0 || walls < 0 ||
            triangles * 3 > Array.MaxLength || r.BaseStream.Length != 20 + triangles * 18)
            throw new InvalidDataException("Invalid dungeon mesh header");
        var vertices = new Vector3[(int)(triangles * 3)];
        float zmin = floors == 0 ? 0 : float.MaxValue, zmax = floors == 0 ? 0 : float.MinValue;
        for (int i = 0; i < vertices.Length; i++)
        {
            // The mesh stores east, height and south in centimetres relative to cx and cy.
            float east = r.ReadInt16() / 100f, z = r.ReadInt16() / 100f, south = r.ReadInt16() / 100f;
            vertices[i] = new Vector3(cx + east, cy - south, z);
            if (i < floors * 3)
            {
                zmin = MathF.Min(zmin, z);
                zmax = MathF.Max(zmax, z);
            }
        }
        return new Mesh3D { Vertices = vertices, FloorTris = floors, ZMin = zmin, ZMax = zmax };
    }
}

/// <summary>An orbit camera around a target, with heights stretched by <see cref="K"/> so levels stand apart.</summary>
internal sealed class Camera3D
{
    public readonly Vector3 Eye, Right, Up, Fwd;
    public readonly float Focal, Cx, Cy, K;
    public readonly int W, H;
    public const float Near = 0.5f;

    /// <param name="target">What the camera looks at, landblock-local metres.</param>
    /// <param name="yaw">Which way the camera faces, radians clockwise from north.</param>
    /// <param name="pitch">How far it looks down, radians.</param>
    public Camera3D(Vector3 target, float yaw, float pitch, float dist, float k, int w, int h)
    {
        K = k; W = w; H = h;
        var t = new Vector3(target.X, target.Y, target.Z * k);
        var ahead = new Vector3(MathF.Sin(yaw), MathF.Cos(yaw), 0);
        Eye = t - ahead * (dist * MathF.Cos(pitch)) + new Vector3(0, 0, dist * MathF.Sin(pitch));
        Fwd = Vector3.Normalize(t - Eye);
        Right = Vector3.Normalize(Vector3.Cross(Fwd, Vector3.UnitZ));
        Up = Vector3.Cross(Right, Fwd);
        Focal = h / 2f / MathF.Tan(22.5f * MathF.PI / 180f);   // 45 degree field of view, as on the web map
        Cx = w / 2f; Cy = h / 2f;
    }

    public Vector3 ToView(Vector3 p)
    {
        Vector3 d = new Vector3(p.X, p.Y, p.Z * K) - Eye;
        return new Vector3(Vector3.Dot(d, Right), Vector3.Dot(d, Up), Vector3.Dot(d, Fwd));
    }

    Vector3 Screen(Vector3 v) => new(Cx + Focal * v.X / v.Z, Cy - Focal * v.Y / v.Z, v.Z);

    public bool Project(Vector3 p, out float sx, out float sy, out float depth)
    {
        Vector3 v = ToView(p);
        depth = v.Z;
        if (v.Z < Near) { sx = sy = 0; return false; }
        Vector3 screen = Screen(v);
        sx = screen.X; sy = screen.Y;
        return true;
    }

    public bool ProjectSegment(Vector3 a, Vector3 b, float cutZ, out Vector3 start, out Vector3 end)
    {
        start = end = default;
        if (!ClipSegment(ref a, ref b, cutZ, keepAbove: false)) return false;
        a = ToView(a); b = ToView(b);
        if (!ClipSegment(ref a, ref b, Near, keepAbove: true)) return false;
        start = Screen(a); end = Screen(b);
        return true;
    }

    static bool ClipSegment(ref Vector3 a, ref Vector3 b, float z, bool keepAbove)
    {
        bool keepA = keepAbove ? a.Z >= z : a.Z <= z;
        bool keepB = keepAbove ? b.Z >= z : b.Z <= z;
        if (keepA == keepB) return keepA;
        Vector3 edge = Vector3.Lerp(a, b, (z - a.Z) / (b.Z - a.Z));
        edge.Z = z;
        if (keepA) b = edge;
        else a = edge;
        return true;
    }
}

/// <summary>What one render needs: the camera, which levels are cut away or dimmed, and supersampling.</summary>
internal readonly record struct RenderJob(Vector3 Target, float Yaw, float Pitch, float Dist, float K, int W, int H, float CutZ, float DimZ, int Supersample);

/// <summary>A finished render: the image as a TGA file, and the nearest depth per pixel for hiding what's behind walls.</summary>
internal sealed class Frame3D
{
    public required RenderJob Job { get; init; }
    public required Camera3D Cam { get; init; }
    public required byte[] Tga { get; init; }
    public required float[] Depth { get; init; }   // distance ahead of the camera, +inf where nothing was drawn

    /// <summary>Whether a point at this screen position and distance is in front of whatever was drawn there.</summary>
    public bool Visible(float sx, float sy, float depth, float slack = 0.8f)
    {
        if (!(sx >= 0 && sy >= 0 && sx < Cam.W && sy < Cam.H)) return true;
        return depth <= Depth[(int)sy * Cam.W + (int)sx] + slack;
    }
}

/// <summary>
/// A small software renderer for the 3D map, in the web map's look: floors shaded by height from violet (deep)
/// to cyan (high) under a sky light and a sun, walls see-through, on a dark blue ground. The plugin canvas only
/// draws 2D, so the scene is rendered here off the tick thread and shown as an image.
/// </summary>
internal sealed class Renderer3D
{
    static readonly Vector3 Deep = Rgb(0x6a52ff), High = Rgb(0x6fd3ff), Scene = Rgb(0x0a0e16);
    static readonly Vector3 Sky = Vector3.One, Ground = Rgb(0x445066);
    static readonly Vector3 Sun = Vector3.Normalize(new Vector3(60, -90, 200));
    const float WallAlpha = 0.34f;

    static Vector3 Rgb(int c) => new(((c >> 16) & 255) / 255f, ((c >> 8) & 255) / 255f, (c & 255) / 255f);

    // Only the current render task uses these buffers. Each finished frame owns its output.
    Vector3[] colour = [];
    float[] inv = [];

    public Frame3D Render(Mesh3D m, RenderJob job)
    {
        int ss = Math.Max(1, job.Supersample), W = job.W * ss, H = job.H * ss;
        var cam = new Camera3D(job.Target, job.Yaw, job.Pitch, job.Dist, job.K, W, H);
        int pixels = W * H;
        if (colour.Length < pixels)
        {
            colour = new Vector3[pixels];
            inv = new float[pixels];
        }
        Array.Fill(colour, Scene, 0, pixels);
        Array.Clear(inv, 0, pixels);

        float span = MathF.Max(6, m.ZMax - m.ZMin);
        Span<Vector3> poly = stackalloc Vector3[8];
        Span<Vector3> tmp = stackalloc Vector3[8];
        for (int pass = 0; pass < 2; pass++)
        {
            bool walls = pass == 1;
            int from = walls ? m.FloorTris : 0, to = walls ? m.Tris : m.FloorTris;
            for (int t = from; t < to; t++)
            {
                int o = t * 3;
                Vector3 a = m.Vertices[o], b = m.Vertices[o + 1], c = m.Vertices[o + 2];
                float zlo = MathF.Min(a.Z, MathF.Min(b.Z, c.Z)), zhi = MathF.Max(a.Z, MathF.Max(b.Z, c.Z));
                if (zlo >= job.CutZ) continue;   // levels above the one shown are cut away

                // Colour by height, lit as three.js lights the web map: a sky light and a sun.
                float zt = Math.Clamp(((a.Z + b.Z + c.Z) / 3 - m.ZMin) / span, 0, 1);
                Vector3 baseCol = Vector3.Lerp(Deep, High, zt);
                if (walls) baseCol = Vector3.Lerp(baseCol, Vector3.One, 0.15f);
                Vector3 sa = new(a.X, a.Y, a.Z * job.K), sb = new(b.X, b.Y, b.Z * job.K), sc = new(c.X, c.Y, c.Z * job.K);
                Vector3 n = Vector3.Cross(sb - sa, sc - sa);
                float nl = n.Length(); if (nl < 1e-6f) continue;
                n /= nl;
                if (Vector3.Dot(n, cam.Eye - sa) < 0) n = -n;   // both sides lit, as the web map's DoubleSide
                Vector3 light = Vector3.Lerp(Ground, Sky, 0.5f * n.Z + 0.5f) * 0.85f + new Vector3(MathF.Max(0, Vector3.Dot(n, Sun)) * 0.55f);
                Vector3 col = Vector3.Min(baseCol * light, Vector3.One);
                if (zhi < job.DimZ) col *= 0.45f;   // levels below the one shown sit back

                poly[0] = a; poly[1] = b; poly[2] = c;
                int count = 3;
                if (zhi > job.CutZ)
                {
                    count = Clip(poly[..count], tmp, job.CutZ, keepAbove: false);
                    tmp[..count].CopyTo(poly);
                }
                for (int i = 0; i < count; i++) poly[i] = cam.ToView(poly[i]);
                count = Clip(poly[..count], tmp, Camera3D.Near, keepAbove: true);
                if (count < 3) continue;
                for (int i = 0; i < count; i++)
                {
                    Vector3 v = tmp[i];
                    tmp[i] = new Vector3(cam.Cx + cam.Focal * v.X / v.Z, cam.Cy - cam.Focal * v.Y / v.Z, 1f / v.Z);
                }
                for (int i = 1; i + 1 < count; i++)
                    Fill(tmp[0], tmp[i], tmp[i + 1], col, walls, W, H);
            }
        }

        // Downsample to the output size and write a TGA, which the host can decode.
        int w = job.W, h = job.H;
        var tga = new byte[18 + w * h * 4];
        tga[2] = 2;                                   // uncompressed true colour
        tga[12] = (byte)w; tga[13] = (byte)(w >> 8);
        tga[14] = (byte)h; tga[15] = (byte)(h >> 8);
        tga[16] = 32; tga[17] = 0x28;                 // 32 bits, 8 of alpha, rows from the top
        var depth = new float[w * h];
        float norm = 255f / (ss * ss);
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
            {
                Vector3 sum = Vector3.Zero; float near = 0;
                for (int j = 0; j < ss; j++)
                    for (int i = 0; i < ss; i++)
                    {
                        int k = (y * ss + j) * W + x * ss + i;
                        sum += colour[k]; near = MathF.Max(near, inv[k]);
                    }
                int q = 18 + (y * w + x) * 4;
                tga[q] = (byte)Math.Min(255, sum.Z * norm + 0.5f);
                tga[q + 1] = (byte)Math.Min(255, sum.Y * norm + 0.5f);
                tga[q + 2] = (byte)Math.Min(255, sum.X * norm + 0.5f);
                tga[q + 3] = 255;
                depth[y * w + x] = near > 0 ? 1f / near : float.PositiveInfinity;
            }
        return new Frame3D { Job = job, Cam = new Camera3D(job.Target, job.Yaw, job.Pitch, job.Dist, job.K, w, h), Tga = tga, Depth = depth };
    }

    /// <summary>Clips a polygon at a constant z. Returns the corner count written to <paramref name="dst"/>.</summary>
    static int Clip(ReadOnlySpan<Vector3> src, Span<Vector3> dst, float z, bool keepAbove)
    {
        int m = 0;
        for (int i = 0; i < src.Length; i++)
        {
            Vector3 p = src[i], q = src[(i + 1) % src.Length];
            bool pin = keepAbove ? p.Z >= z : p.Z <= z;
            bool qin = keepAbove ? q.Z >= z : q.Z <= z;
            if (pin) dst[m++] = p;
            if (pin != qin) dst[m++] = Vector3.Lerp(p, q, (z - p.Z) / (q.Z - p.Z));
        }
        return m;
    }

    /// <summary>Fills one screen triangle (x, y, 1/depth). Floors are opaque and hide what's behind them. Walls are see-through, as on the web map: they blend in front of floors and hide nothing.</summary>
    void Fill(Vector3 a, Vector3 b, Vector3 c, Vector3 col, bool blend, int W, int H)
    {
        float area = (b.X - a.X) * (c.Y - a.Y) - (b.Y - a.Y) * (c.X - a.X);
        if (MathF.Abs(area) < 1e-6f) return;
        if (area < 0) { (b, c) = (c, b); area = -area; }
        int x0 = Math.Max(0, (int)MathF.Floor(MathF.Min(a.X, MathF.Min(b.X, c.X))));
        int x1 = Math.Min(W - 1, (int)MathF.Ceiling(MathF.Max(a.X, MathF.Max(b.X, c.X))));
        int y0 = Math.Max(0, (int)MathF.Floor(MathF.Min(a.Y, MathF.Min(b.Y, c.Y))));
        int y1 = Math.Min(H - 1, (int)MathF.Ceiling(MathF.Max(a.Y, MathF.Max(b.Y, c.Y))));
        if (x0 > x1 || y0 > y1) return;
        float ia = 1f / area;
        Vector3 wallMix = col * WallAlpha;
        for (int y = y0; y <= y1; y++)
        {
            float py = y + 0.5f;
            for (int x = x0; x <= x1; x++)
            {
                float px = x + 0.5f;
                float w0 = (c.X - b.X) * (py - b.Y) - (c.Y - b.Y) * (px - b.X);
                float w1 = (a.X - c.X) * (py - c.Y) - (a.Y - c.Y) * (px - c.X);
                float w2 = (b.X - a.X) * (py - a.Y) - (b.Y - a.Y) * (px - a.X);
                if (w0 < 0 || w1 < 0 || w2 < 0) continue;
                float z = (w0 * a.Z + w1 * b.Z + w2 * c.Z) * ia;
                int k = y * W + x;
                if (blend)
                {
                    if (z < inv[k] * 0.999f) continue;
                    colour[k] = colour[k] * (1 - WallAlpha) + wallMix;
                }
                else
                {
                    if (z <= inv[k]) continue;
                    inv[k] = z; colour[k] = col;
                }
            }
        }
    }
}
