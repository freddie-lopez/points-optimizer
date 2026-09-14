import json, math
d = json.load(open('package/land-110m.json'))
sx, sy = d['transform']['scale']; tx, ty = d['transform']['translate']
arcs = []
for arc in d['arcs']:
    x = y = 0; pts = []
    for dx, dy in arc:
        x += dx; y += dy
        pts.append((x * sx + tx, y * sy + ty))
    arcs.append(pts)

W, H = 1000, 520  # viewBox; Miller-ish equirectangular with lat clamp
def proj(lon, lat):
    lat = max(min(lat, 83), -60)
    X = (lon + 180) / 360 * W
    # Miller cylindrical
    phi = math.radians(lat)
    Y = 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * phi))
    ymax = 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(83)))
    ymin = 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(-60)))
    return X, (ymax - Y) / (ymax - ymin) * H

def ring(indexes):
    pts = []
    for i in indexes:
        a = arcs[~i][::-1] if i < 0 else arcs[i]
        pts.extend(a if not pts else a[1:])
    return pts

def path_for(geom):
    polys = geom['arcs'] if geom['type'] == 'MultiPolygon' else [geom['arcs']]
    out = []
    for poly in polys:
        for r in poly:
            raw = ring(r)
            if len(raw) < 3: continue
            if max(la for lo,la in raw) < -58: continue  # Antarctica
            # split at antimeridian jumps
            segs=[[]]
            for i,(lo,la) in enumerate(raw):
                if i and abs(lo-raw[i-1][0])>180: segs.append([])
                segs[-1].append(proj(lo,la))
            for pts in segs:
                if len(pts)<2: continue
                out.append('M' + ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts) + ('Z' if len(segs)==1 else ''))
    return ''.join(out)

paths = []
for g in d['objects']['land']['geometries']:
    paths.append(path_for(g))
open('land.path', 'w').write(''.join(paths))
open('proj.json', 'w').write(json.dumps({'W': W, 'H': H}))
print(len(''.join(paths)))
