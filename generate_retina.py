"""
Medically accurate 4096×4096 retinal fundus texture map — healthy right eye.
Anatomy: optic disc + cup + neuroretinal rim, macula, fovea, foveal avascular
zone reflex, superior/inferior temporal arcades (artery + vein pairs),
nasal vessels, choroidal tessellation, RNFL striations, RPE granularity,
fundus-camera radial illumination, circular vignette border.
"""

import math, random, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SIZE = 4096
SEED = 2024
random.seed(SEED)
rng  = np.random.default_rng(SEED)

W = H = SIZE
cx, cy = W // 2, H // 2

# ── Anatomical landmarks — right eye, standard fundus view ───────────────────
#   Optic disc: nasal (left of centre)
DISC_X  = int(cx - W * 0.178)
DISC_Y  = cy
DISC_R  = int(W * 0.041)          # ~168 px
CUP_R   = int(DISC_R * 0.40)      # C/D ratio 0.40 (normal ≤ 0.5)

#   Macula / fovea: temporal (right of centre), same horizontal
MAC_X   = int(cx + W * 0.130)
MAC_Y   = cy
MAC_R   = int(W * 0.075)          # ~307 px  (≈ 1.8 disc diameters)
FOV_R   = int(W * 0.018)          # ~74 px foveal avascular zone

print(f"Generating {W}×{H} retinal texture …")
print(f"  Disc ({DISC_X},{DISC_Y}) r={DISC_R}  Cup r={CUP_R}")
print(f"  Macula ({MAC_X},{MAC_Y}) r={MAC_R}  Fovea r={FOV_R}")

# ── Coordinate grids ─────────────────────────────────────────────────────────
Yg, Xg = np.mgrid[0:H, 0:W].astype(np.float32)

def dist(px, py):
    return np.sqrt((Xg - px)**2 + (Yg - py)**2)

# ============================================================================
#  1. BASE RETINAL BACKGROUND
# ============================================================================

# Choroidal tessellation: coarse low-freq Perlin-like pattern
def make_coarse_noise(rows, cols, freq, amp, rng):
    small = rng.normal(0, 1, (freq, freq)).astype(np.float32)
    pil   = Image.fromarray(((small - small.min()) /
                              (small.max() - small.min() + 1e-9) * 255).astype(np.uint8))
    pil   = pil.resize((cols, rows), Image.BICUBIC)
    return np.array(pil).astype(np.float32) / 255.0 * amp

choroid = (make_coarse_noise(H, W, 32, 18, rng) +
           make_coarse_noise(H, W, 64,  9, rng) +
           make_coarse_noise(H, W, 128, 5, rng))

img = np.empty((H, W, 3), dtype=np.float32)
img[:,:,0] = 178 + choroid          # R 178-205
img[:,:,1] =  68 + choroid * 0.42   # G  68-76
img[:,:,2] =  36 + choroid * 0.20   # B  36-40

# ── Fundus-camera radial illumination hot-spot (brighter centre) ─────────────
d_ctr   = dist(cx, cy)
hotspot = np.exp(-0.5 * (d_ctr / (W * 0.32))**2) * 28
img[:,:,0] += hotspot
img[:,:,1] += hotspot * 0.48
img[:,:,2] += hotspot * 0.22

# ── Subtle RNFL (nerve fibre layer) brightness near disc ─────────────────────
d_disc  = dist(DISC_X, DISC_Y)
nfl_glow = np.exp(-0.5 * (d_disc / (DISC_R * 3.2))**2) * 14
img[:,:,0] += nfl_glow
img[:,:,1] += nfl_glow * 0.35

# ── RNFL striation texture (fine radial streaks centred on disc) ─────────────
angle_from_disc = np.arctan2(Yg - DISC_Y, Xg - DISC_X)
striation = np.sin(angle_from_disc * 48) * 2.5
nfl_zone  = np.clip(1.0 - (d_disc / (DISC_R * 5.0)), 0, 1)**1.5
img[:,:,0] += striation * nfl_zone
img[:,:,1] += striation * nfl_zone * 0.4

img = np.clip(img, 0, 255)

# ============================================================================
#  2. MACULA  (annular darker region, RPE thickening)
# ============================================================================
d_mac = dist(MAC_X, MAC_Y)

# Outer macular darkening (Gaussian annulus)
mac_mask = np.exp(-0.5 * (d_mac / (MAC_R * 0.62))**2)
img[:,:,0] -= mac_mask * 42
img[:,:,1] -= mac_mask * 24
img[:,:,2] -= mac_mask * 11

# Inner macular ring (slightly lighter than fovea)
mac_ring = np.exp(-0.5 * ((d_mac - FOV_R * 2.2) / (FOV_R * 1.0))**2)
img[:,:,0] -= mac_ring * 10
img[:,:,1] -= mac_ring *  6

# ============================================================================
#  3. FOVEA  (deepest pit, avascular zone)
# ============================================================================
d_fov = dist(MAC_X, MAC_Y)   # same centre as macula

# Foveal pit
fov_mask = np.exp(-0.5 * (d_fov / (FOV_R * 0.85))**2)
img[:,:,0] -= fov_mask * 32
img[:,:,1] -= fov_mask * 18
img[:,:,2] -= fov_mask *  8

# Foveal reflex highlight (tiny central bright spot — photoreceptor reflex)
fov_reflex = np.exp(-0.5 * (d_fov / (FOV_R * 0.16))**2)
img[:,:,0] += fov_reflex * 38
img[:,:,1] += fov_reflex * 14
img[:,:,2] += fov_reflex *  6

img = np.clip(img, 0, 255)

# ============================================================================
#  4. BLOOD VESSELS
# ============================================================================
pil = Image.fromarray(img.astype(np.uint8), 'RGB')
draw = ImageDraw.Draw(pil)

# ── Catmull-Rom spline ────────────────────────────────────────────────────────
def catmull_seg(p0, p1, p2, p3, n=24):
    pts = []
    for i in range(n):
        t = i / (n - 1)
        t2, t3 = t*t, t*t*t
        for k in (0, 1):
            pts.append(0.5 * (
                2*p1[k] +
                (-p0[k] + p2[k]) * t +
                (2*p0[k] - 5*p1[k] + 4*p2[k] - p3[k]) * t2 +
                (-p0[k] + 3*p1[k] - 3*p2[k] + p3[k]) * t3
            ))
    coords = [(pts[i*2], pts[i*2+1]) for i in range(n)]
    return coords

def spline(ctrl, n_per=24):
    if len(ctrl) < 2:
        return ctrl
    p = [ctrl[0]] + list(ctrl) + [ctrl[-1]]
    out = []
    for i in range(1, len(p)-2):
        out += catmull_seg(p[i-1], p[i], p[i+1], p[i+2], n_per)
    return out

# ── Tapered vessel drawing ────────────────────────────────────────────────────
def draw_vessel_pts(pts, w_start, w_end, color):
    n = len(pts)
    if n < 2:
        return
    for i in range(n - 1):
        t   = i / max(n - 2, 1)
        w   = max(1, int(w_start + (w_end - w_start) * t))
        draw.line([(int(pts[i][0]),   int(pts[i][1])),
                   (int(pts[i+1][0]), int(pts[i+1][1]))],
                  fill=color, width=w)
    # Light reflex along artery centre (2/3 brightness, 40% width)
    if w_start >= 4:
        wr = max(1, int(w_start * 0.38))
        lr = tuple(min(255, c + 28) for c in color)
        for i in range(n - 1):
            t  = i / max(n - 2, 1)
            w  = max(1, int(wr + (max(1, int(w_start*0.38*0.6)) - wr) * t))
            draw.line([(int(pts[i][0]),   int(pts[i][1])),
                       (int(pts[i+1][0]), int(pts[i+1][1]))],
                      fill=lr, width=w)

# ── Recursive arcuate branching ───────────────────────────────────────────────
def grow_vessel(x0, y0, angle_deg, length, width,
                depth=0, max_depth=8, is_artery=True, curve_sign=1):
    """
    curve_sign: +1 vessel above MAC_Y curves downward (toward macula)
                -1 vessel below MAC_Y curves upward
    """
    if depth >= max_depth or width < 0.5 or length < 8:
        return

    n_steps   = 14
    step_len  = length / n_steps
    # Arcuate curvature: stronger for temporal vessels (toward macula)
    arc_base  = 0.30 if depth < 3 else 0.14

    angle = math.radians(angle_deg)
    ctrl  = [(x0, y0)]
    x, y  = x0, y0

    for _ in range(n_steps):
        # Curve toward or away from macula equator
        if y < MAC_Y:
            angle += math.radians(arc_base * curve_sign)
        else:
            angle -= math.radians(arc_base * curve_sign)
        angle += random.gauss(0, 0.015)
        x += step_len * math.cos(angle)
        y += step_len * math.sin(angle)
        ctrl.append((x, y))

    smooth_pts = spline(ctrl, n_per=20)

    # Colour: artery = brighter red, vein = darker maroon
    base_lum = max(22, 105 - depth * 13)
    if is_artery:
        color = (base_lum, int(base_lum * 0.16), int(base_lum * 0.10))
    else:
        base_lum = max(18, 82 - depth * 11)
        color = (base_lum, int(base_lum * 0.09), int(base_lum * 0.22))

    w_end = max(0.5, width * 0.65)
    draw_vessel_pts(smooth_pts, width, w_end, color)

    if depth < max_depth - 1:
        # Branch point at ~62% along
        mid = int(len(smooth_pts) * 0.62)
        bx, by = smooth_pts[min(mid, len(smooth_pts)-1)]
        # Angle at branch point
        m2 = min(mid+1, len(smooth_pts)-1)
        bangle = math.degrees(math.atan2(
            smooth_pts[m2][1] - smooth_pts[mid][1],
            smooth_pts[m2][0] - smooth_pts[mid][0]))

        # Off-branch (perpendicular ± some noise)
        branch_off = random.uniform(28, 52) * (1 if y0 < MAC_Y else -1)
        grow_vessel(bx, by, bangle + branch_off,
                    length * 0.55, width * 0.62,
                    depth+1, max_depth, is_artery, curve_sign)

        # Continue primary from endpoint
        end = smooth_pts[-1]
        eangle = math.degrees(math.atan2(
            smooth_pts[-1][1] - smooth_pts[-2][1],
            smooth_pts[-1][0] - smooth_pts[-2][0]))
        grow_vessel(end[0], end[1], eangle,
                    length * 0.68, width * 0.72,
                    depth+1, max_depth, is_artery, curve_sign)

# Main trunk starts just inside disc rim
de = DISC_R * 0.45   # disc edge offset

# ── Superior temporal arcade  (above MAC_Y, curves down, curve_sign=+1) ──────
grow_vessel(DISC_X+de, DISC_Y-de*0.4, -36, W*0.43, DISC_R*0.29,
            is_artery=True,  curve_sign=+1)   # sup-temp artery
grow_vessel(DISC_X+de, DISC_Y-de*0.6, -30, W*0.41, DISC_R*0.26,
            is_artery=False, curve_sign=+1)   # sup-temp vein

# ── Inferior temporal arcade  (below MAC_Y, curves up, curve_sign=-1) ────────
grow_vessel(DISC_X+de, DISC_Y+de*0.4,  36, W*0.43, DISC_R*0.29,
            is_artery=True,  curve_sign=-1)   # inf-temp artery
grow_vessel(DISC_X+de, DISC_Y+de*0.6,  30, W*0.41, DISC_R*0.26,
            is_artery=False, curve_sign=-1)   # inf-temp vein

# ── Superior nasal  (goes left-upward, slight curve) ─────────────────────────
grow_vessel(DISC_X-de*0.4, DISC_Y-de, -148, W*0.29, DISC_R*0.21,
            is_artery=True,  curve_sign=+1)
grow_vessel(DISC_X-de*0.6, DISC_Y-de, -154, W*0.27, DISC_R*0.19,
            is_artery=False, curve_sign=+1)

# ── Inferior nasal ─────────────────────────────────────────────────────────────
grow_vessel(DISC_X-de*0.4, DISC_Y+de,  148, W*0.29, DISC_R*0.21,
            is_artery=True,  curve_sign=-1)
grow_vessel(DISC_X-de*0.6, DISC_Y+de,  154, W*0.27, DISC_R*0.19,
            is_artery=False, curve_sign=-1)

# ── Papillo-macular bundle (thin vessels between disc and fovea) ──────────────
grow_vessel(DISC_X+de, DISC_Y-de*0.1,  -8, W*0.17, DISC_R*0.12,
            max_depth=5, is_artery=True, curve_sign=0)
grow_vessel(DISC_X+de, DISC_Y+de*0.1,   4, W*0.17, DISC_R*0.11,
            max_depth=5, is_artery=False, curve_sign=0)

print("  Blood vessels drawn")

img = np.array(pil).astype(np.float32)

# ============================================================================
#  5. OPTIC DISC
# ============================================================================
d_disc = dist(DISC_X, DISC_Y)

# Scleral ring / disc border (subtle dark annulus just outside rim)
border = ((d_disc > DISC_R * 0.96) & (d_disc < DISC_R * 1.10)).astype(np.float32)
img[:,:,0] -= border * 22
img[:,:,1] -= border * 12
img[:,:,2] -= border *  6

# Neuroretinal rim (warm pink-orange, brightest at ISNT sectors)
rim_mask = np.where(d_disc < DISC_R,
                    np.exp(-0.5 * ((d_disc - DISC_R*0.60) / (DISC_R*0.32))**2), 0)
img[:,:,0] += rim_mask * 62
img[:,:,1] += rim_mask * 40
img[:,:,2] += rim_mask * 24

# Central cup (bright yellow-white; lamina cribrosa texture)
cup_mask = np.where(d_disc < DISC_R,
                    np.exp(-0.5 * (d_disc / (CUP_R * 0.92))**2), 0)
img[:,:,0] += cup_mask * 80
img[:,:,1] += cup_mask * 65
img[:,:,2] += cup_mask * 42

# Lamina cribrosa: faint dot pattern inside cup
lc_noise = rng.normal(0, 1, (H, W)).astype(np.float32)
lc_dots  = (np.sin(Xg * 0.22) * np.sin(Yg * 0.22)) * 6
cup_zone = (d_disc < CUP_R * 0.88).astype(np.float32)
img[:,:,0] += cup_zone * lc_dots
img[:,:,1] += cup_zone * lc_dots * 0.8
img[:,:,2] += cup_zone * lc_dots * 0.5

img = np.clip(img, 0, 255)

# ============================================================================
#  6. VIGNETTING — circular fundus camera field boundary
# ============================================================================
d_field = dist(cx, cy)
field_r = W * 0.452
# Hard circular crop fading to black
vignette = np.clip(1.0 - ((d_field - field_r * 0.78) / (field_r * 0.22)), 0, 1)
vignette = vignette[:,:, np.newaxis]
img *= vignette
img = np.clip(img, 0, 255)

# ============================================================================
#  7. RPE / PHOTORECEPTOR GRANULARITY
# ============================================================================
# Fine grain (photoreceptor mosaic)
fine_noise = rng.normal(0, 3.2, (H, W, 3)).astype(np.float32)
img = np.clip(img + fine_noise, 0, 255)

# Medium grain (RPE cells)
med_noise = rng.normal(0, 1.5, (H//2, W//2, 3)).astype(np.float32)
med_pil   = Image.fromarray(((med_noise - med_noise.min()) /
                               (med_noise.max() - med_noise.min()+1e-9) * 255
                               ).astype(np.uint8).clip(0,255))
med_pil   = med_pil.resize((W, H), Image.BICUBIC)
med_arr   = (np.array(med_pil).astype(np.float32) / 255.0 - 0.5) * 5
img = np.clip(img + med_arr, 0, 255)

# ============================================================================
#  8. FINAL OUTPUT
# ============================================================================
result = Image.fromarray(img.astype(np.uint8), 'RGB')

# Mild unsharp mask to crisp up vessel edges
result = result.filter(ImageFilter.UnsharpMask(radius=1.4, percent=40, threshold=4))

out_path = "retina_healthy_4096.png"
result.save(out_path, 'PNG')
print(f"  Saved: {out_path}  ({W}x{H} px, RGB)")

# Also save a 512-px preview for quick checking
preview = result.resize((512, 512), Image.LANCZOS)
preview.save("retina_healthy_preview.jpg", 'JPEG', quality=92)
print("  Preview: retina_healthy_preview.jpg  (512x512)")
