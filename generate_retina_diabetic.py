"""
Medically accurate 4096×4096 retinal fundus texture — diabetic retinopathy (right eye).
Anatomy & pathology:
  - Background retina (yellowed/ischemic orange-red)
  - Optic disc (pallor, ghost-like)
  - Tortuous, widened, irregular blood vessels + neovascularisation (NVD)
  - Dot & blot hemorrhages  (scattered, concentrated near arcades)
  - Flame hemorrhages along nerve fibre layer
  - Hard exudates (waxy yellow clusters near macula — circinate pattern)
  - Microaneurysms (tiny dark-red dots along vessel walls)
  - Macular edema (diffuse swelling — desaturated, lighter macular zone)
  - Cotton-wool spots (fluffy white NFl infarcts)
  - Venous beading (irregular vein calibre)
"""

import math, random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SIZE = 4096
SEED = 9871
random.seed(SEED)
rng  = np.random.default_rng(SEED)

W = H = SIZE
cx, cy = W // 2, H // 2

# ── Landmarks (right eye) ────────────────────────────────────────────────────
DISC_X = int(cx - W * 0.178)
DISC_Y = cy
DISC_R = int(W * 0.041)
CUP_R  = int(DISC_R * 0.52)   # larger cup — disc pallor in advanced RD

MAC_X  = int(cx + W * 0.130)
MAC_Y  = cy
MAC_R  = int(W * 0.075)
FOV_R  = int(W * 0.018)

print(f"Generating {W}×{H} diabetic retinopathy texture …")

Yg, Xg = np.mgrid[0:H, 0:W].astype(np.float32)
def dist(px, py):
    return np.sqrt((Xg - px)**2 + (Yg - py)**2)

# ============================================================================
#  1. BASE RETINAL BACKGROUND  (yellower, more ischemic than healthy)
# ============================================================================
def coarse_noise(rows, cols, freq, amp, rng):
    s = rng.normal(0, 1, (freq, freq)).astype(np.float32)
    p = Image.fromarray(((s - s.min())/(s.max()-s.min()+1e-9)*255).astype(np.uint8))
    p = p.resize((cols, rows), Image.BICUBIC)
    return np.array(p).astype(np.float32)/255.0 * amp

choroid = (coarse_noise(H, W, 32, 20, rng) +
           coarse_noise(H, W, 64, 10, rng) +
           coarse_noise(H, W, 128, 5, rng))

img = np.empty((H, W, 3), dtype=np.float32)
# More yellow-orange (ischemia / lipid deposition tint)
img[:,:,0] = 184 + choroid          # R
img[:,:,1] =  78 + choroid * 0.50   # G (more yellow than healthy)
img[:,:,2] =  38 + choroid * 0.18   # B

# Radial hot-spot (fundus camera)
d_ctr   = dist(cx, cy)
hotspot = np.exp(-0.5*(d_ctr/(W*0.34))**2) * 24
img[:,:,0] += hotspot
img[:,:,1] += hotspot * 0.52
img[:,:,2] += hotspot * 0.20

img = np.clip(img, 0, 255)

# ============================================================================
#  2. MACULAR EDEMA (diffuse, lighter, slightly desaturated zone)
# ============================================================================
d_mac = dist(MAC_X, MAC_Y)

# Edema: retinal swelling → slightly lighter, less pigmented appearance
edema_mask = np.exp(-0.5*(d_mac/(MAC_R*0.70))**2)
img[:,:,0] += edema_mask * 18
img[:,:,1] += edema_mask * 12
img[:,:,2] += edema_mask *  8
# Desaturate slightly
edema_gray = (img[:,:,0]*0.3 + img[:,:,1]*0.59 + img[:,:,2]*0.11)
for c in range(3):
    img[:,:,c] = img[:,:,c]*(1-edema_mask*0.18) + edema_gray*edema_mask*0.18

# Foveal thickening (edema blurs/lifts fovea — no pit)
fov_edema = np.exp(-0.5*(d_mac/(FOV_R*1.4))**2)
img[:,:,0] += fov_edema * 10
img[:,:,1] += fov_edema *  7
img[:,:,2] += fov_edema *  5

img = np.clip(img, 0, 255)

# ============================================================================
#  3. DRAW PATHOLOGICAL VESSELS + ALL LESIONS
# ============================================================================
pil  = Image.fromarray(img.astype(np.uint8), 'RGB')
draw = ImageDraw.Draw(pil)

# ── Catmull-Rom spline ────────────────────────────────────────────────────────
def catmull_seg(p0,p1,p2,p3,n=22):
    pts=[]
    for i in range(n):
        t=i/(n-1); t2=t*t; t3=t2*t
        for k in(0,1):
            pts.append(0.5*(2*p1[k]+(-p0[k]+p2[k])*t+
                            (2*p0[k]-5*p1[k]+4*p2[k]-p3[k])*t2+
                            (-p0[k]+3*p1[k]-3*p2[k]+p3[k])*t3))
    return [(pts[i*2],pts[i*2+1]) for i in range(n)]

def spline(ctrl,n_per=22):
    if len(ctrl)<2: return ctrl
    p=[ctrl[0]]+list(ctrl)+[ctrl[-1]]
    out=[]
    for i in range(1,len(p)-2):
        out+=catmull_seg(p[i-1],p[i],p[i+1],p[i+2],n_per)
    return out

def draw_vessel_pts(pts, w_start, w_end, color, beading=False):
    n=len(pts)
    if n<2: return
    for i in range(n-1):
        t   = i/max(n-2,1)
        # Venous beading: irregular width
        bead = (1 + 0.30*math.sin(i*0.9)) if beading else 1.0
        w   = max(1, int((w_start+(w_end-w_start)*t)*bead))
        draw.line([(int(pts[i][0]),int(pts[i][1])),
                   (int(pts[i+1][0]),int(pts[i+1][1]))],
                  fill=color, width=w)

# ── Tortuous diabetic vessel growth ─────────────────────────────────────────
def grow_vessel(x0,y0,angle_deg,length,width,
                depth=0,max_depth=7,is_artery=True,curve_sign=1,beading=False):
    if depth>=max_depth or width<0.5 or length<8: return

    n_steps  = 14
    step_len = length/n_steps
    arc_base = 0.32 if depth<3 else 0.16
    # Extra tortuosity in diabetic vessels
    tort = 0.06 + depth*0.008

    angle = math.radians(angle_deg)
    ctrl  = [(x0,y0)]
    x,y   = x0,y0
    for _ in range(n_steps):
        if y < MAC_Y:
            angle += math.radians(arc_base*curve_sign)
        else:
            angle -= math.radians(arc_base*curve_sign)
        angle += random.gauss(0, tort)  # more tortuous than healthy
        x += step_len*math.cos(angle)
        y += step_len*math.sin(angle)
        ctrl.append((x,y))

    smooth_pts = spline(ctrl,n_per=20)

    base_lum = max(22, 100-depth*12)
    if is_artery:
        color = (base_lum, int(base_lum*0.15), int(base_lum*0.09))
    else:
        # Veins darker + purplish (venous dilation in RD)
        base_lum = max(18, 78-depth*10)
        color = (base_lum, int(base_lum*0.08), int(base_lum*0.24))

    # Diabetic vessels are thickened
    w_end = max(0.5, width*0.68)
    draw_vessel_pts(smooth_pts, width, w_end, color, beading=beading)

    if depth < max_depth-1:
        mid = int(len(smooth_pts)*0.62)
        bx,by = smooth_pts[min(mid,len(smooth_pts)-1)]
        m2 = min(mid+1,len(smooth_pts)-1)
        bangle = math.degrees(math.atan2(
            smooth_pts[m2][1]-smooth_pts[mid][1],
            smooth_pts[m2][0]-smooth_pts[mid][0]))

        branch_off = random.uniform(25,55)*(1 if y0<MAC_Y else -1)
        grow_vessel(bx,by,bangle+branch_off,
                    length*0.52,width*0.60,
                    depth+1,max_depth,is_artery,curve_sign,beading)

        end=smooth_pts[-1]
        eangle=math.degrees(math.atan2(
            smooth_pts[-1][1]-smooth_pts[-2][1],
            smooth_pts[-1][0]-smooth_pts[-2][0]))
        grow_vessel(end[0],end[1],eangle,
                    length*0.65,width*0.70,
                    depth+1,max_depth,is_artery,curve_sign,beading)

de = DISC_R*0.45

# Main vessels (tortuous, thickened, with venous beading on veins)
grow_vessel(DISC_X+de, DISC_Y-de*0.4, -36, W*0.43, DISC_R*0.31, is_artery=True,  curve_sign=+1)
grow_vessel(DISC_X+de, DISC_Y-de*0.6, -30, W*0.41, DISC_R*0.28, is_artery=False, curve_sign=+1, beading=True)
grow_vessel(DISC_X+de, DISC_Y+de*0.4,  36, W*0.43, DISC_R*0.31, is_artery=True,  curve_sign=-1)
grow_vessel(DISC_X+de, DISC_Y+de*0.6,  30, W*0.41, DISC_R*0.28, is_artery=False, curve_sign=-1, beading=True)
grow_vessel(DISC_X-de*0.4, DISC_Y-de, -148, W*0.28, DISC_R*0.22, is_artery=True,  curve_sign=+1)
grow_vessel(DISC_X-de*0.6, DISC_Y-de, -154, W*0.26, DISC_R*0.20, is_artery=False, curve_sign=+1)
grow_vessel(DISC_X-de*0.4, DISC_Y+de,  148, W*0.28, DISC_R*0.22, is_artery=True,  curve_sign=-1)
grow_vessel(DISC_X-de*0.6, DISC_Y+de,  154, W*0.26, DISC_R*0.20, is_artery=False, curve_sign=-1)

# NVD: Neovascularisation at disc (thin, erratic new vessels)
def grow_nv(x0,y0,angle_deg,length,width,depth=0,max_depth=5):
    if depth>=max_depth or width<0.3: return
    angle = math.radians(angle_deg)
    ctrl  = [(x0,y0)]; x,y=x0,y0
    for _ in range(10):
        angle += random.gauss(0, 0.18)   # very erratic
        x += (length/10)*math.cos(angle)
        y += (length/10)*math.sin(angle)
        ctrl.append((x,y))
    pts = spline(ctrl,n_per=15)
    color = (180,30,20)   # bright red — new vessels
    draw_vessel_pts(pts, width, width*0.5, color)
    if depth<max_depth-1:
        for off in [40,-40,80]:
            grow_nv(pts[-1][0],pts[-1][1],
                    angle_deg+off+random.gauss(0,15),
                    length*0.55, width*0.60, depth+1, max_depth)

# Several NVD fronds from disc
for ang in [-20,10,40,-50,70]:
    grow_nv(DISC_X+random.randint(-20,20),
            DISC_Y+random.randint(-20,20),
            ang, DISC_R*3.5, 3, max_depth=5)

print("  Vessels + neovascularisation drawn")

# ── DOT HEMORRHAGES  (dark round blot, ~40-160 px diam at 4096) ───────────────
hem_positions = []
# Concentrated in posterior pole, near arcades
arcade_zones = [
    # (cx_zone, cy_zone, spread_x, spread_y, count)
    (DISC_X + W*0.10, DISC_Y - H*0.08, W*0.18, H*0.08, 9),   # sup temporal
    (DISC_X + W*0.10, DISC_Y + H*0.08, W*0.18, H*0.08, 8),   # inf temporal
    (MAC_X  - W*0.04, MAC_Y - H*0.05, W*0.10, H*0.06, 6),    # peri-macular sup
    (MAC_X  - W*0.04, MAC_Y + H*0.05, W*0.10, H*0.06, 5),    # peri-macular inf
    (DISC_X - W*0.06, DISC_Y,         W*0.12, H*0.10, 4),    # nasal
    (cx,             DISC_Y - H*0.14, W*0.20, H*0.06, 5),    # mid-periphery sup
    (cx,             DISC_Y + H*0.14, W*0.20, H*0.06, 5),    # mid-periphery inf
]

for hcx,hcy,sx,sy,count in arcade_zones:
    for _ in range(count):
        hx = int(hcx + rng.normal(0, sx*0.4))
        hy = int(hcy + rng.normal(0, sy*0.4))
        r  = int(rng.integers(28, 90))   # dot/blot hemorrhage
        # Slightly irregular shape
        rx = int(r * rng.uniform(0.7, 1.0))
        ry = int(r * rng.uniform(0.7, 1.0))
        color = (int(rng.integers(40,80)), int(rng.integers(3,14)),
                 int(rng.integers(2,10)))
        draw.ellipse([hx-rx, hy-ry, hx+rx, hy+ry], fill=color)
        hem_positions.append((hx,hy))

# ── FLAME HEMORRHAGES (along RNFL — elongated, feathery) ─────────────────────
for _ in range(12):
    hx = DISC_X + int(rng.integers(-DISC_R*6, DISC_R*8))
    hy = DISC_Y + int(rng.integers(-DISC_R*5, DISC_R*5))
    angle = rng.uniform(0, 2*math.pi)
    lx = int(rng.integers(60, 160))
    ly = int(rng.integers(18, 45))
    color = (int(rng.integers(60,100)), int(rng.integers(4,16)), int(rng.integers(2,8)))
    # Draw as rotated ellipse (approximate with polygon)
    import math as _m
    pts_flame = []
    for t in range(36):
        a = t/36*2*_m.pi
        ex = hx + lx*_m.cos(a)*_m.cos(angle) - ly*_m.sin(a)*_m.sin(angle)
        ey = hy + lx*_m.cos(a)*_m.sin(angle) + ly*_m.sin(a)*_m.cos(angle)
        pts_flame.append((ex,ey))
    draw.polygon(pts_flame, fill=color)

print("  Hemorrhages drawn")

# ── HARD EXUDATES  (bright yellow-white waxy clusters — circinate around MAC) ─
# Hard exudates form a partial ring (circinate pattern) around macula
exudate_clusters = [
    (MAC_X-W*0.03, MAC_Y-H*0.055, 14),
    (MAC_X+W*0.02, MAC_Y-H*0.040,  9),
    (MAC_X-W*0.05, MAC_Y+H*0.050, 11),
    (MAC_X+W*0.04, MAC_Y+H*0.030,  8),
    (MAC_X-W*0.07, MAC_Y-H*0.020, 10),
    (MAC_X-W*0.06, MAC_Y+H*0.015,  7),
    (DISC_X+W*0.08, DISC_Y-H*0.05, 6),
    (DISC_X+W*0.10, DISC_Y+H*0.06, 5),
]

for ecx,ecy,n_dots in exudate_clusters:
    for _ in range(n_dots):
        ex = int(ecx + rng.normal(0, W*0.018))
        ey = int(ecy + rng.normal(0, H*0.018))
        r  = int(rng.integers(14, 42))
        # Hard exudates: bright yellow-white, waxy
        brightness = int(rng.integers(210, 252))
        yellow     = int(rng.integers(190, 238))
        color = (brightness, yellow, int(rng.integers(55, 95)))
        draw.ellipse([ex-r, ey-r, ex+r, ey+r], fill=color)

print("  Hard exudates drawn")

# ── COTTON-WOOL SPOTS  (fluffy white RNFL infarcts near disc) ─────────────────
cws_positions = [
    (DISC_X+W*0.07, DISC_Y-H*0.07),
    (DISC_X+W*0.05, DISC_Y+H*0.09),
    (DISC_X-W*0.06, DISC_Y-H*0.05),
    (DISC_X+W*0.13, DISC_Y-H*0.04),
    (DISC_X+W*0.09, DISC_Y+H*0.06),
]
for (cwx, cwy) in cws_positions:
    cwx += int(rng.normal(0, W*0.008))
    cwy += int(rng.normal(0, H*0.008))
    r = int(rng.integers(40, 80))
    # Multiple overlapping soft circles for fluffy look
    for _ in range(6):
        ox = cwx + int(rng.integers(-r//2, r//2))
        oy = cwy + int(rng.integers(-r//2, r//2))
        or_ = int(r * rng.uniform(0.5, 1.0))
        # White-grey, slightly transparent look
        c = int(rng.integers(195, 230))
        draw.ellipse([ox-or_, oy-or_, ox+or_, oy+or_], fill=(c, c, c))

print("  Cotton-wool spots drawn")

# ── MICROANEURYSMS  (tiny dark-red dots, ≤12 px diam) ────────────────────────
ma_zones = [
    (DISC_X+W*0.08, DISC_Y, W*0.22, H*0.14, 35),
    (MAC_X,         MAC_Y,  W*0.12, H*0.10, 22),
    (cx,            cy,     W*0.28, H*0.20, 20),
]
for mcx,mcy,sx,sy,count in ma_zones:
    for _ in range(count):
        mx = int(mcx + rng.normal(0, sx*0.35))
        my = int(mcy + rng.normal(0, sy*0.35))
        r  = int(rng.integers(4, 13))
        color = (int(rng.integers(70,120)), int(rng.integers(4,18)),
                 int(rng.integers(4,14)))
        draw.ellipse([mx-r, my-r, mx+r, my+r], fill=color)

print("  Microaneurysms drawn")

img = np.array(pil).astype(np.float32)

# ============================================================================
#  4. OPTIC DISC  (pallor — less pink, more white/grey)
# ============================================================================
d_disc = dist(DISC_X, DISC_Y)

border = ((d_disc>DISC_R*0.96)&(d_disc<DISC_R*1.10)).astype(np.float32)
img[:,:,0] -= border*20; img[:,:,1] -= border*11; img[:,:,2] -= border*6

# Rim (paler than healthy — early optic atrophy)
rim = np.where(d_disc<DISC_R, np.exp(-0.5*((d_disc-DISC_R*0.62)/(DISC_R*0.30))**2),0)
img[:,:,0] += rim*45; img[:,:,1] += rim*38; img[:,:,2] += rim*28

# Cup (larger, whiter — glaucomatous-like pallor in advanced RD)
cup = np.where(d_disc<DISC_R, np.exp(-0.5*(d_disc/(CUP_R*0.94))**2),0)
img[:,:,0] += cup*72; img[:,:,1] += cup*64; img[:,:,2] += cup*50

img = np.clip(img, 0, 255)

# ============================================================================
#  5. VIGNETTING
# ============================================================================
d_field  = dist(cx, cy)
field_r  = W*0.452
vignette = np.clip(1.0 - ((d_field-field_r*0.78)/(field_r*0.22)), 0, 1)[:,:,np.newaxis]
img *= vignette
img = np.clip(img, 0, 255)

# ============================================================================
#  6. RPE GRANULARITY + FINAL POLISH
# ============================================================================
fine_noise = rng.normal(0, 3.5, (H, W, 3)).astype(np.float32)
img = np.clip(img + fine_noise, 0, 255)

result = Image.fromarray(img.astype(np.uint8), 'RGB')
result = result.filter(ImageFilter.UnsharpMask(radius=1.2, percent=35, threshold=4))

out_path = "retina_diabetic_4096.png"
result.save(out_path, 'PNG')
print(f"  Saved: {out_path}  ({W}x{H} px, RGB)")

preview = result.resize((512,512), Image.LANCZOS)
preview.save("retina_diabetic_preview.jpg", 'JPEG', quality=92)
print("  Preview: retina_diabetic_preview.jpg  (512x512)")
