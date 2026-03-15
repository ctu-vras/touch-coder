from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# Update these paths/settings here instead of using CLI arguments.
SOURCE_IMAGE = Path("icons/3d_zones/diagram.png")
OUTPUT_DIR = Path("icons/3d_zones/generated_masks")
ARCHIVE_DIR = OUTPUT_DIR / "_merged_legacy"
ZONE_NAME_PREFIX = "ZONE_"
THRESHOLD = 200
MIN_ZONE_AREA = 250
INCLUDE_OUTSIDE_MASK = True
INCLUDE_LINE_MASK = True

# Merge regions after you inspect zone_index_preview.png.
# Keys are output names, values are 1-based region indices to combine.
MERGED_ZONE_GROUPS: dict[str, list[int]] = {
    "FACE": [1, 2, 3, 4, 5, 6],
}

# Stable names for single regions that should remain separate.
MANUAL_ZONE_NAMES: dict[int, str] = {
    7: "NECK",
    8: "L_SHOULDER",
    9: "R_SHOULDER",
    10: "L_ELBOW",
    11: "R_ELBOW",
    12: "BELLY",
    13: "L_WRIST",
    14: "R_WRIST",
    15: "R_HIP",
    16: "L_HIP",
    17: "R_KNEE",
    18: "L_KNEE",
    19: "BOX1",
    20: "BOX2",
    21: "BOX4",
    22: "BOX3",
    23: "R_ANKLE",
    24: "L_ANKLE",
    25: "BOX6",
    26: "BOX5",
}


def load_diagram_mask(image_path: Path, threshold: int) -> tuple[list[list[bool]], int, int]:
    image = Image.open(image_path).convert("L")
    width, height = image.size
    pixels = image.load()

    # True means black outline pixel, False means fillable white space.
    solid = [[pixels[x, y] < threshold for x in range(width)] for y in range(height)]
    return solid, width, height


def extract_regions(
    solid: list[list[bool]], width: int, height: int
) -> tuple[list[dict[str, object]], list[tuple[int, int]]]:
    visited = [[False] * width for _ in range(height)]
    regions: list[dict[str, object]] = []
    outside_points: list[tuple[int, int]] = []

    for y in range(height):
        for x in range(width):
            if solid[y][x] or visited[y][x]:
                continue

            queue = deque([(x, y)])
            visited[y][x] = True
            points: list[tuple[int, int]] = []
            touches_border = False
            min_x = max_x = x
            min_y = max_y = y
            sum_x = 0
            sum_y = 0

            while queue:
                cx, cy = queue.popleft()
                points.append((cx, cy))
                sum_x += cx
                sum_y += cy
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)

                if cx == 0 or cy == 0 or cx == width - 1 or cy == height - 1:
                    touches_border = True

                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < width and 0 <= ny < height and not solid[ny][nx] and not visited[ny][nx]:
                        visited[ny][nx] = True
                        queue.append((nx, ny))

            if touches_border:
                outside_points.extend(points)
                continue

            area = len(points)
            if area < MIN_ZONE_AREA:
                continue

            regions.append(
                {
                    "points": points,
                    "area": area,
                    "bbox": [min_x, min_y, max_x, max_y],
                    "centroid": [round(sum_x / area, 2), round(sum_y / area, 2)],
                }
            )

    regions.sort(key=lambda region: (region["centroid"][1], region["centroid"][0]))
    return regions, outside_points


def zone_name(index: int) -> str:
    return MANUAL_ZONE_NAMES.get(index, f"{ZONE_NAME_PREFIX}{index:02d}")


def merged_member_indices() -> set[int]:
    members: set[int] = set()
    for indices in MERGED_ZONE_GROUPS.values():
        members.update(indices)
    return members


def save_mask(path: Path, width: int, height: int, black_pixels: list[tuple[int, int]]) -> None:
    image = Image.new("L", (width, height), color=255)
    pixels = image.load()
    for x, y in black_pixels:
        pixels[x, y] = 0
    image.save(path)


def save_preview(
    source_image: Path,
    output_path: Path,
    regions: list[dict[str, object]],
) -> None:
    preview = Image.open(source_image).convert("RGB")
    draw = ImageDraw.Draw(preview)
    font = ImageFont.load_default()

    for index, region in enumerate(regions, start=1):
        cx, cy = region["centroid"]
        label = str(index)
        text_bbox = draw.textbbox((0, 0), label, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        x0 = int(cx - text_width / 2) - 2
        y0 = int(cy - text_height / 2) - 2
        x1 = x0 + text_width + 4
        y1 = y0 + text_height + 4
        draw.rectangle((x0, y0, x1, y1), fill=(255, 255, 0))
        draw.text((x0 + 2, y0 + 2), label, fill=(255, 0, 0), font=font)

    preview.save(output_path)


def main() -> None:
    if not SOURCE_IMAGE.exists():
        raise FileNotFoundError(f"Source diagram not found: {SOURCE_IMAGE}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    solid, width, height = load_diagram_mask(SOURCE_IMAGE, THRESHOLD)
    regions, outside_points = extract_regions(solid, width, height)

    line_points = [(x, y) for y in range(height) for x in range(width) if solid[y][x]]

    region_map = {index: region for index, region in enumerate(regions, start=1)}
    merged_indices = merged_member_indices()
    metadata: list[dict[str, object]] = []

    for name, indices in MERGED_ZONE_GROUPS.items():
        points: list[tuple[int, int]] = []
        x_values: list[int] = []
        y_values: list[int] = []
        area = 0
        for index in indices:
            region = region_map[index]
            region_points = region["points"]
            points.extend(region_points)
            area += region["area"]
            x_values.extend(point[0] for point in region_points)
            y_values.extend(point[1] for point in region_points)

        save_mask(OUTPUT_DIR / f"{name}.png", width, height, points)
        metadata.append(
            {
                "merged_indices": indices,
                "name": name,
                "area": area,
                "centroid": [round(sum(x_values) / area, 2), round(sum(y_values) / area, 2)],
                "bbox": [min(x_values), min(y_values), max(x_values), max(y_values)],
            }
        )

    for index, region in region_map.items():
        if index in merged_indices:
            legacy_path = OUTPUT_DIR / f"{ZONE_NAME_PREFIX}{index:02d}.png"
            if legacy_path.exists():
                legacy_path.replace(ARCHIVE_DIR / legacy_path.name)
            continue

        name = zone_name(index)
        save_mask(OUTPUT_DIR / f"{name}.png", width, height, region["points"])
        metadata.append(
            {
                "index": index,
                "name": name,
                "area": region["area"],
                "centroid": region["centroid"],
                "bbox": region["bbox"],
            }
        )

    if INCLUDE_OUTSIDE_MASK:
        save_mask(OUTPUT_DIR / "OUTSIDE.png", width, height, outside_points)

    if INCLUDE_LINE_MASK:
        save_mask(OUTPUT_DIR / "LINE.png", width, height, line_points)

    save_preview(SOURCE_IMAGE, OUTPUT_DIR / "zone_index_preview.png", regions)

    metadata_path = OUTPUT_DIR / "zones.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Saved {len(metadata)} zone entries to {OUTPUT_DIR}")
    print(f"Preview image: {OUTPUT_DIR / 'zone_index_preview.png'}")
    print(f"Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
