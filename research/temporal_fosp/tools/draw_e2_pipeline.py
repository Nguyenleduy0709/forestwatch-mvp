"""Draw the E2 pipeline in the style of the FoSp paper figure (Focus / Separation / Fusion), with the parts E2 adds.

Assets (an .npz with cur, ref, gt, focus, focus_rev, prob, chg as 512x512 arrays) come from one real test frame run
through model B; see the session notes.  Output: <out>.png (and .svg, .pdf).

  python -I research/temporal_fosp/tools/draw_e2_pipeline.py --assets work/figures/e2_example.npz --out work/figures/e2_pipeline
"""
import argparse

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle  # noqa: E402

INK = '#1a1a1a'
FOCUS_BOX, FOCUS_EDGE = '#dce6f2', '#5b7fb3'
BCG_BOX, BCG_EDGE = '#d9f3f6', '#3aa7b9'
SEP_BOX, SEP_EDGE = '#f6d7bd', '#cf8a51'
NEW_BOX, NEW_EDGE = '#d4ecd5', '#3d8b40'
FUS_BOX, FUS_EDGE = '#ecdff0', '#9061a8'
SLOTS = [3.35, 4.75, 6.15, 7.55, 8.95, 10.35, 11.75, 13.15, 14.55]  # box, box, cube, box, cube, box, cube, box, cube
BOX_W, CUBE_W, CUBE_D = 0.65, 0.35, 0.45


def shade(color, k):
    c = np.array(matplotlib.colors.to_rgb(color))
    return tuple(c + (1 - c) * k) if k > 0 else tuple(c * (1 + k))


def box(ax, x, y, w, h, text, fc, ec, rot=0, fs=10, bold=False, r=0.12, z=3):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}', fc=fc, ec=ec, lw=1.4, zorder=z))
    ax.text(x + w / 2, y + h / 2, text, rotation=rot, ha='center', va='center', fontsize=fs, color=INK,
            fontweight='bold' if bold else 'normal', zorder=z + 1, linespacing=1.1)


def cube(ax, x, yc, h, color, label=None, where='above', fs=12):
    y = yc - h / 2
    dy = CUBE_D * 0.8
    ax.add_patch(Rectangle((x, y), CUBE_W, h, fc=color, ec='none', zorder=3))
    ax.add_patch(Polygon([(x, y + h), (x + CUBE_D, y + h + dy), (x + CUBE_W + CUBE_D, y + h + dy), (x + CUBE_W, y + h)],
                         closed=True, fc=shade(color, 0.35), ec='none', zorder=3))
    ax.add_patch(Polygon([(x + CUBE_W, y), (x + CUBE_W + CUBE_D, y + dy), (x + CUBE_W + CUBE_D, y + h + dy), (x + CUBE_W, y + h)],
                         closed=True, fc=shade(color, -0.18), ec='none', zorder=3))
    if label:
        tx, ty = {'above': (x + 0.3, y + h + dy + 0.22), 'below': (x + 0.3, y - 0.28),
                  'left': (x - 0.22, y + h + dy - 0.05), 'right': (x + 1.08, yc + 0.32)}[where]
        ax.text(tx, ty, label, ha='center', va='center', fontsize=fs, color=INK, zorder=5)
    return x + CUBE_W / 2, y, y + h + dy  # front centre x, bottom, top


def arrow(ax, p, q, color=INK, lw=1.2, ls='-', ms=11, z=2):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle='-|>', mutation_scale=ms, lw=lw, color=color, linestyle=ls,
                                 shrinkA=0, shrinkB=0, zorder=z))


def line(ax, pts, color=INK, lw=1.2, ls='-', z=2):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, ls=ls, zorder=z, solid_capstyle='butt')


def minus(ax, x, y, ec=INK):
    ax.add_patch(Circle((x, y), 0.17, fc='white', ec=ec, lw=1.4, zorder=5))
    ax.plot([x - 0.1, x + 0.1], [y, y], color=ec, lw=1.6, zorder=6)


def image(ax, img, x0, y0, w, h, cmap=None, edge='#555555'):
    ax.imshow(img, extent=(x0, x0 + w, y0, y0 + h), cmap=cmap, zorder=4, interpolation='lanczos', vmin=0, vmax=1 if cmap else None)
    ax.add_patch(Rectangle((x0, y0), w, h, fc='none', ec=edge, lw=1.0, zorder=5))


def chain(ax, yc, first, second, blocks, colors, labels, fc, ec, where='above', second_shape='box'):
    """Embedding, a second block, then cube / block / cube / block / cube / block / cube.  Returns cube geometry."""
    box(ax, SLOTS[0], yc - 0.7, BOX_W, 1.4, first, fc, ec, rot=270, fs=10)
    if second_shape == 'trap':
        x = SLOTS[1]
        ax.add_patch(Polygon([(x, yc - 0.7), (x, yc + 0.7), (x + BOX_W, yc + 0.42), (x + BOX_W, yc - 0.42)], closed=True,
                             fc=fc, ec=ec, lw=1.4, zorder=3))
        ax.text(x + BOX_W / 2, yc, second, rotation=270, ha='center', va='center', fontsize=9.5, color=INK, zorder=4, linespacing=1.1)
    else:
        box(ax, SLOTS[1], yc - 0.7, BOX_W, 1.4, second, fc, ec, rot=270, fs=9.5)
    arrow(ax, (SLOTS[0] + BOX_W, yc), (SLOTS[1], yc))
    cubes = []
    heights = [1.6, 1.35, 1.1, 0.85] if blocks == 'focus' else [0.75, 1.0, 1.25, 1.45]
    for k in range(4):
        cx = SLOTS[2 + 2 * k]
        cubes.append(cube(ax, cx, yc, heights[k], colors[k], labels[k], where=where))
        prev_right = SLOTS[1 + 2 * k] + BOX_W
        arrow(ax, (prev_right, yc), (cx, yc))
        if k < 3:
            bx = SLOTS[3 + 2 * k]
            name = (f'Transformer\nBlock {k + 2}' if blocks == 'focus' else f'Decoder\nBlock {k + 1}')
            box(ax, bx, yc - 0.7, BOX_W, 1.4, name, fc, ec, rot=270, fs=9.5)
            arrow(ax, (cx + CUBE_W + CUBE_D * 0.5, yc), (bx, yc))
    return cubes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--assets', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    d = np.load(a.assets)
    cur, ref, focus, focus_rev, prob, chg = d['cur'], d['ref'], d['focus'], d['focus_rev'], d['prob'], d['chg']
    pred = np.zeros((*prob.shape, 3))
    pred[prob > 0.4] = (0.85, 0.08, 0.08)
    change = np.clip(chg / np.percentile(chg, 99.7), 0, 1)

    fig = plt.figure(figsize=(24 * 0.62, 13.4 * 0.62))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 24)
    ax.set_ylim(0, 13.4)
    ax.axis('off')

    # panels
    ax.add_patch(FancyBboxPatch((3.0, 8.55), 14.75, 4.35, boxstyle='round,pad=0,rounding_size=0.35', fc='#e9eef8', ec='none', zorder=0))
    ax.add_patch(FancyBboxPatch((3.0, 4.0), 14.75, 4.35, boxstyle='round,pad=0,rounding_size=0.35', fc='#fbefe5', ec='none', zorder=0))
    ax.add_patch(FancyBboxPatch((3.0, 0.2), 14.75, 3.65, boxstyle='round,pad=0,rounding_size=0.35', fc='#ebf6ec', ec='#5aa469',
                                lw=1.6, ls=(0, (5, 3)), zorder=0))
    ax.text(15.45, 11.95, 'Focus', fontsize=13.5, fontweight='bold', color=INK)
    ax.text(15.45, 11.5, 'giữ nguyên FoSp', fontsize=10, color='#444444')
    ax.text(15.45, 7.45, 'Separation', fontsize=13.5, fontweight='bold', color=INK)
    ax.text(15.45, 7.05, 'giữ nguyên FoSp,\nMAT đóng băng', fontsize=9.5, color='#444444', va='top')
    ax.text(15.45, 2.55, 'Nền quan sát', fontsize=13.5, fontweight='bold', color='#2e6b31')
    ax.text(15.45, 2.15, 'mới (E2),\ncùng MAT đóng băng', fontsize=9.5, color='#2e6b31', va='top')

    # inputs
    image(ax, cur, 0.15, 6.05, 2.2, 2.2)
    ax.text(1.25, 5.8, 'Ảnh hiện tại $I_t$', ha='center', fontsize=11, color=INK)
    ax.text(1.25, 5.5, 'khói 12 phút sau khi cháy', ha='center', fontsize=8.5, color='#555555')
    image(ax, ref, 0.15, 1.45, 2.0, 2.0, edge='#3d8b40')
    ax.text(1.15, 1.2, 'Ảnh quá khứ $R$', ha='center', fontsize=11, color='#2e6b31')
    ax.text(1.15, 0.95, 'trung vị các khung\n20–40 phút trước\nkhi cháy, đã căn', ha='center', va='top', fontsize=8.2,
            color='#2e6b31', linespacing=1.15)
    line(ax, [(2.35, 7.15), (2.75, 7.15)])
    line(ax, [(2.75, 11.1), (2.75, 4.85)])
    for y in (11.1, 7.0, 4.85):
        arrow(ax, (2.75, y), (SLOTS[0], y))
    arrow(ax, (2.15, 2.45), (SLOTS[0], 2.45), color='#2e6b31')
    ax.text(2.42, 4.36, 'Blank', ha='right', va='center', fontsize=9.5, color=INK)
    arrow(ax, (2.5, 4.38), (SLOTS[0], 4.38))
    ax.text(2.72, 1.74, 'Blank', ha='center', va='center', fontsize=9.5, color='#2e6b31')
    arrow(ax, (3.04, 1.76), (SLOTS[0], 1.76), color='#2e6b31')

    # Focus branch
    fo = chain(ax, 11.1, 'Embedding', 'Transformer\nBlock 1', 'focus', ['#1f4e79', '#2e75b6', '#9dc3e6', '#c7dcf0'],
               ['$F^{O}_{4}$', '$F^{O}_{3}$', '$F^{O}_{2}$', '$F^{O}_{1}$'], FOCUS_BOX, FOCUS_EDGE, where='left')
    box(ax, 8.0, 9.0, 6.0, 0.85, 'Bidirectional Cascade Generator', BCG_BOX, BCG_EDGE, fs=12)
    line(ax, [(fo[0][0] + 0.15, fo[0][1]), (fo[0][0] + 0.15, 9.5)])
    arrow(ax, (fo[0][0] + 0.15, 9.5), (8.0, 9.5))
    for k in (1, 2):
        arrow(ax, (fo[k][0], fo[k][1]), (fo[k][0], 9.85))
    line(ax, [(fo[3][0], fo[3][1]), (fo[3][0], 9.62)])
    arrow(ax, (fo[3][0], 9.62), (14.0, 9.62))
    for c in fo:  # multi-level features to Fusion
        line(ax, [(c[0] + 0.35, c[2]), (c[0] + 0.35, 12.62)])
    line(ax, [(fo[0][0] + 0.35, 12.62), (19.75, 12.62), (19.75, 10.25)])
    arrow(ax, (19.75, 10.4), (19.75, 10.15))
    image(ax, focus, 15.4, 8.7, 1.4, 1.4, cmap='gray')
    ax.text(16.1, 10.27, 'Focus map', ha='center', fontsize=10.5, color=INK)
    arrow(ax, (14.0, 9.2), (15.4, 9.2))
    image(ax, focus_rev, 4.95, 8.68, 1.2, 1.2, cmap='gray')
    ax.text(4.05, 9.6, 'Focus map\n(Re.)', ha='center', va='center', fontsize=9.5, color=INK)
    arrow(ax, (8.0, 9.17), (6.15, 9.17))
    ax.text(7.07, 8.86, 'Reverse & Binary', ha='center', fontsize=8.5, color=INK)
    line(ax, [(5.55, 8.68), (5.55, 8.18), (SLOTS[0] + 0.32, 8.18)])
    arrow(ax, (SLOTS[0] + 0.32, 8.18), (SLOTS[0] + 0.32, 7.7))

    # Separation: N (masked by the focus map) and A (blank) through the frozen MAT
    fn = chain(ax, 7.0, 'Embedding', 'Conv\nBlock', 'sep', ['#f4c6a0', '#e8853d', '#a0522d', '#7f7f7f'],
               ['$F^{N}_{1}$', '$F^{N}_{2}$', '$F^{N}_{3}$', '$F^{N}_{4}$'], SEP_BOX, SEP_EDGE, second_shape='trap')
    fa = chain(ax, 4.85, 'Embedding', 'Conv\nBlock', 'sep', ['#e8e8e3', '#cfcfcf', '#b5b5b5', '#9a9a9a'],
               ['$F^{A}_{1}$', '$F^{A}_{2}$', '$F^{A}_{3}$', '$F^{A}_{4}$'], SEP_BOX, SEP_EDGE, where='right', second_shape='trap')
    fr = chain(ax, 2.25, 'Embedding', 'Conv\nBlock', 'sep', ['#cfe9cf', '#93cf95', '#5aa469', '#2e7d32'],
               ['$F^{R}_{1}$', '$F^{R}_{2}$', '$F^{R}_{3}$', '$F^{R}_{4}$'], NEW_BOX, NEW_EDGE, where='below', second_shape='trap')
    ax.add_patch(FancyBboxPatch((5.85, 5.73), 10.45, 0.44, boxstyle='round,pad=0,rounding_size=0.12', fc='#e3e3e3', ec='#9a9a9a',
                                lw=1.2, ls=(0, (4, 3)), zorder=1))
    ax.add_patch(FancyBboxPatch((5.85, 3.28), 10.45, 0.44, boxstyle='round,pad=0,rounding_size=0.12', fc='#d4ecd5', ec='#5aa469',
                                lw=1.2, ls=(0, (4, 3)), zorder=1))
    for k in range(4):
        x = fn[k][0]
        minus(ax, x, 5.95)
        arrow(ax, (x, fn[k][1]), (x, 6.12))
        arrow(ax, (x, fa[k][2] - CUBE_D * 0.8), (x, 5.78))
        ax.text(x + 0.42, 5.95, f'$F^{{F}}_{{{k + 1}}}$', ha='center', va='center', fontsize=12, color=INK, zorder=6)
        minus(ax, x, 3.5, ec='#2e6b31')
        arrow(ax, (x, fa[k][1]), (x, 3.67), color='#2e6b31')
        arrow(ax, (x, fr[k][2] - CUBE_D * 0.8), (x, 3.33), color='#2e6b31')
        ax.text(x + 0.5, 3.5, f'$F^{{Obs}}_{{{k + 1}}}$', ha='center', va='center', fontsize=12, color='#2e6b31', zorder=6)
    ax.text(16.42, 6.17, 'khác nền đoán', ha='left', fontsize=8.5, color='#555555')
    ax.text(16.42, 3.66, 'khác nền thật', ha='left', fontsize=8.5, color='#2e6b31')

    # reliability gate (new)
    gx0, gx1 = 17.92, 21.98
    gxc = (gx0 + gx1) / 2
    ax.add_patch(FancyBboxPatch((gx0, 3.3), gx1 - gx0, 3.95, boxstyle='round,pad=0,rounding_size=0.2', fc=NEW_BOX, ec=NEW_EDGE, lw=1.8, zorder=3))
    ax.text(gxc, 6.92, 'Cổng tin cậy (mới)', ha='center', fontsize=12.5, fontweight='bold', color='#1f4d22', zorder=4)
    image(ax, cur, gxc - 1.55, 5.68, 0.8, 0.8)
    image(ax, ref, gxc - 0.15, 5.68, 0.8, 0.8, edge='#3d8b40')
    ax.text(gxc - 0.45, 6.08, '⇄', ha='center', va='center', fontsize=13, color=INK, zorder=6)
    ax.text(gxc + 0.85, 6.08, 'r, g', ha='left', va='center', fontsize=11, color=INK, zorder=6)
    gate_lines = [
        ('trọng số $w_i$ cho từng pixel, ở mỗi tầng', 5.35),
        ('nhìn $|F^{F}_i|$, $|F^{Obs}_i|$, focus map, r, g', 4.99),
        ('ghép delta:', 4.6),
        ('$\\mathrm{Conv}(F^{F}_i) + \\mathrm{Conv}_0(w_i \\odot (F^{Obs}_i - F^{F}_i))$', 4.22),
        ('không có $R$: $w_i = 0$, đúng FoSp', 3.66),
    ]
    for txt, y in gate_lines:
        ax.text(gxc, y, txt, ha='center', va='center', fontsize=9.2, color=INK, zorder=4)
    arrow(ax, (16.3, 5.95), (gx0, 5.95))
    arrow(ax, (16.3, 3.5), (gx0, 3.5), color='#2e6b31')
    arrow(ax, (19.75, 7.25), (19.75, 8.85))
    ax.text(19.9, 8.05, 'đặc trưng khói\nđã ghép', ha='left', va='center', fontsize=8.5, color='#444444')

    # fusion, head, output
    box(ax, 18.6, 8.85, 2.3, 1.3, 'Fusion', FUS_BOX, FUS_EDGE, fs=14)
    arrow(ax, (16.8, 9.4), (18.6, 9.4))
    x = 21.15
    ax.add_patch(Polygon([(x, 8.75), (x, 10.25), (x + 0.8, 9.9), (x + 0.8, 9.1)], closed=True, fc=FUS_BOX, ec=FUS_EDGE, lw=1.4, zorder=3))
    ax.text(x + 0.4, 9.5, 'Cls\nHead', rotation=270, ha='center', va='center', fontsize=10, zorder=4, linespacing=1.1)
    arrow(ax, (20.9, 9.5), (21.15, 9.5))
    arrow(ax, (21.95, 9.5), (22.25, 9.5))
    image(ax, pred, 22.25, 8.7, 1.6, 1.6)
    ax.text(23.05, 8.42, 'Mask khói', ha='center', fontsize=10.5, color=INK)

    # inset: the idea at image level
    ax.add_patch(FancyBboxPatch((18.0, 0.2), 5.9, 2.75, boxstyle='round,pad=0,rounding_size=0.3', fc='none', ec='#9a9a9a',
                                lw=1.6, ls=(0, (6, 4)), zorder=1))
    ax.text(20.95, 2.62, 'Ý tưởng ở mức ảnh', ha='center', fontsize=10.5, color=INK)
    image(ax, cur, 18.2, 0.8, 1.45, 1.45)
    minus(ax, 20.0, 1.52)
    image(ax, ref, 20.35, 0.8, 1.45, 1.45, edge='#3d8b40')
    arrow(ax, (21.9, 1.52), (22.25, 1.52))
    image(ax, change, 22.3, 0.8, 1.45, 1.45, cmap='gray')
    ax.text(20.95, 0.45, 'khói là thứ mới so với ảnh quá khứ', ha='center', fontsize=9, color='#444444')

    # legend
    for i, (fc, ec, txt) in enumerate(((FOCUS_BOX, FOCUS_EDGE, 'giữ nguyên FoSp'), (SEP_BOX, SEP_EDGE, 'MAT inpainting, đóng băng'),
                                        (NEW_BOX, NEW_EDGE, 'phần E2 thêm vào'))):
        y = 12.55 - i * 0.5
        ax.add_patch(FancyBboxPatch((0.2, y - 0.15), 0.42, 0.3, boxstyle='round,pad=0,rounding_size=0.05', fc=fc, ec=ec, lw=1.2))
        ax.text(0.75, y, txt, va='center', fontsize=9.5, color=INK)

    for ext in ('png', 'svg', 'pdf'):
        fig.savefig(f'{a.out}.{ext}', dpi=220 if ext == 'png' else None, facecolor='white')
    print('wrote', a.out + '.{png,svg,pdf}')


if __name__ == '__main__':
    main()
