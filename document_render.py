"""Render and inspect the local viewer's aligned pages without a browser."""

from functools import lru_cache
from io import BytesIO
from itertools import combinations

from PIL import Image, ImageChops, ImageDraw

from desktop.bridge import page_path, versions
from page_alignment import comparison_pages, footer_regions
from preview import page_dimensions, preview_png, requires_poppler


def preview_layout(document_id, ids):
    records = versions(document_id, ids)
    paths = tuple(page_path(record) for record in records)
    dimensions = [page_dimensions(path) for path in paths]
    pairs, sources = comparison_pages(document_id, ids, paths, list(map(len, dimensions)))
    footers = footer_regions(paths, pairs)
    return records, paths, dimensions, pairs, sources, footers


def describe_page(position, pair, source):
    return {
        "position": position, "pages": pair, "alignment_source": source,
        "kind": ("inserted" if pair[0] is None else "deleted" if pair[1] is None else "paired")
        if len(pair) == 2 else "page",
    }


def box_union(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def box_area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def merge_regions(regions, size):
    width, height = size
    if len(regions) > 64:
        bins = {}
        for box in regions:
            key = (box[0] + box[2]) * 4 // width, (box[1] + box[3]) * 4 // height
            bins[key] = box_union(bins.get(key, box), box)
        regions = [bins[key] for key in sorted(bins, key=lambda key: (key[1], key[0]))]

    def added(i, j):
        a, b = regions[i], regions[j]
        intersection = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
        return box_area(box_union(a, b)) - box_area(a) - box_area(b) + box_area(intersection)

    while len(regions) > 7:
        i, j = min(combinations(range(len(regions)), 2), key=lambda pair: added(*pair))
        regions[i] = box_union(regions[i], regions[j])
        regions.pop(j)
    coverage = Image.new("L", size)
    draw = ImageDraw.Draw(coverage)
    for x0, y0, x1, y1 in regions:
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    if coverage.histogram()[255] * 5 >= width * height * 4:
        return [(0, 0, 1, 1)]
    return [(x0 / width, y0 / height, x1 / width, y1 / height)
            for x0, y0, x1, y1 in sorted(regions, key=lambda r: (r[1], r[0], r[3], r[2]))]


def changed_regions(before, after):
    if before.size != after.size:
        return [(0, 0, 1, 1)]
    red, green, blue = ImageChops.difference(before, after).split()
    mask = ImageChops.lighter(red, ImageChops.lighter(green, blue)).point(
        [0] * 28 + [255] * 228, "L")
    bounds = mask.getbbox()
    if bounds is None:
        return []
    # Match the browser's threshold of three changed pixels per 12 px tile.
    tile = 12
    width, height = mask.size
    dirty = set()
    for y in range(bounds[1] // tile, (bounds[3] - 1) // tile + 1):
        for x in range(bounds[0] // tile, (bounds[2] - 1) // tile + 1):
            box = (x * tile, y * tile, min((x + 1) * tile, width), min((y + 1) * tile, height))
            if mask.crop(box).histogram()[255] >= 3:
                dirty.add((x, y))
    regions = []
    for start in sorted(dirty, key=lambda cell: (cell[1], cell[0])):
        if start not in dirty:
            continue
        dirty.remove(start)
        x, y = start
        left = right = x
        top = bottom = y
        queue = [(x, y)]
        for x, y in queue:
            left, top, right, bottom = box_union((left, top, right, bottom), (x, y, x, y))
            for row in range(y - 1, y + 2):
                for column in range(x - 1, x + 2):
                    cell = (column, row)
                    if cell in dirty:
                        queue.append(cell)
                        dirty.remove(cell)
        regions.append((left * tile, top * tile, min(width, (right + 1) * tile), min(height, (bottom + 1) * tile)))
    return merge_regions(regions, mask.size) if regions else []


def page_images(paths, pair, prefer_pdfium):
    return [Image.open(BytesIO(preview_png(path, page, prefer_pdfium=prefer_pdfium))).convert("RGB")
            if page is not None else None for path, page in zip(paths, pair)]


@lru_cache(maxsize=128)
def visual_difference(paths, pair, footer, prefer_pdfium):
    if paths[0] == paths[1] and pair[0] == pair[1]:
        return [], False
    return compare_images(page_images(paths, pair, prefer_pdfium), footer) if None not in pair else (
        [(0, 0, 1, 1)], False)


def compare_images(images, footer):
    if None in images:
        return [(0, 0, 1, 1)], False
    regions = changed_regions(*images)
    only_footer = bool(footer and regions and all(
        x0 >= footer[0] and y0 >= footer[1] and x1 <= footer[2] and y1 <= footer[3]
        for x0, y0, x1, y1 in regions))
    return regions, only_footer


def use_pdfium(paths):
    return not any(requires_poppler(path) for path in paths)


def render_page(document_id, ids, position, max_side, highlight=True, change=None):
    records, paths, _, pairs, sources, footers = preview_layout(document_id, ids)
    if not 1 <= position <= len(pairs):
        raise ValueError(f"페이지 위치는 1~{len(pairs)} 범위에서 선택하세요.")
    pair = tuple(pairs[position - 1])
    prefer_pdfium = use_pdfium(paths)
    images = page_images(paths, pair, prefer_pdfium)
    regions, footer_only = compare_images(images, footers[position - 1]) if len(pair) == 2 else ([], False)
    if change is not None and (footer_only or not 1 <= change <= len(regions)):
        raise ValueError("이 위치에 해당 번호의 본문 변경 영역이 없습니다.")
    for index, page in enumerate(images):
        if page is None:
            continue
        page.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(page, "RGBA")
        for number, (left, top, right, bottom) in enumerate(regions, 1):
            x, y = int(left * page.width), int(top * page.height)
            box = (x, y, max(x, round(right * page.width) - 1), max(y, round(bottom * page.height) - 1))
            if highlight:
                color = (219, 45, 76, 64) if index == 0 else (38, 156, 96, 69)
                draw.rectangle(box, fill=color)
            if number == change:
                draw.rectangle(box, outline=(22, 88, 179, 255), width=3)
    if len(images) == 1:
        image = images[0]
    else:
        width = max(page.width for page in images if page is not None)
        height = max(page.height for page in images if page is not None)
        heading = 34
        font_size = min(20, max(12, width // 50))
        image = Image.new("RGB", (2 * width + 12, height + heading), "#edf1f6")
        draw = ImageDraw.Draw(image)
        for index, page in enumerate(images):
            x = index * (width + 12)
            label = records[index]["version_label"]
            draw.text((x + 8, 7), f"{label}  |  Page {pair[index]}" if page else f"{label}  |  No page",
                      fill="#22354b", font_size=font_size)
            if page is not None:
                image.paste(page, (x, heading))
            else:
                draw.text((x + 8, heading + 10), "No corresponding page in this version.",
                          fill="#465970", font_size=font_size)
    png = BytesIO()
    image.save(png, format="PNG")
    info = describe_page(position, pair, sources[position - 1])
    info.update(version_ids=ids, regions=regions, footer_only=footer_only, image_size=image.size)
    return info, png.getvalue()
