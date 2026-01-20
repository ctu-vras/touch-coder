import base64
import json
import math
import os
import statistics
import webbrowser
from collections import Counter, defaultdict

import pandas as pd
from PIL import Image
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots


LIMBS = ["LH", "RH", "LL", "RL"]


def _normalize_onset(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().upper()
    return s


def _parse_xy_list(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    s = str(value).strip()
    if not s:
        return []
    parts = [p.strip() for p in s.split(",") if p.strip() != ""]
    out = []
    for p in parts:
        try:
            out.append(float(p))
        except ValueError:
            continue
    return out


def _parse_zones(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if isinstance(value, list):
        return value
    s = str(value).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        return parsed if parsed is not None else []
    except json.JSONDecodeError:
        return []


def _flatten_zones(zones):
    flat = []
    for z in zones:
        if isinstance(z, list):
            flat.extend([str(v) for v in z if v is not None])
        elif z is not None:
            flat.append(str(z))
    return flat


def _read_export_df(export_path):
    try:
        df = pd.read_csv(export_path)
        if "Frame" in df.columns:
            return df
    except Exception:
        df = None

    # fallback for older exports with header lines
    try:
        df = pd.read_csv(export_path, skiprows=6)
        if "Frame" in df.columns:
            return df
    except Exception:
        pass
    raise ValueError(f"Could not read export CSV: {export_path}")


def _load_limb_rows(export_path):
    df = _read_export_df(export_path)
    records = df.to_dict(orient="records")
    limb_rows = {limb: [] for limb in LIMBS}

    for rec in records:
        frame = int(rec.get("Frame", 0))
        for limb in LIMBS:
            limb_rows[limb].append(
                {
                    "Frame": frame,
                    "Onset": _normalize_onset(rec.get(f"{limb}_Onset", "")),
                    "X": _parse_xy_list(rec.get(f"{limb}_X", "")),
                    "Y": _parse_xy_list(rec.get(f"{limb}_Y", "")),
                    "Zones": _parse_zones(rec.get(f"{limb}_Zones", "")),
                }
            )

    total_frames = int(df["Frame"].max()) + 1 if len(df) else 0
    return limb_rows, total_frames


def _compute_limb_metrics(rows):
    total_touches = 0
    touch_durations = []
    onset_count_distribution = defaultdict(int)
    zone_touch_count = defaultdict(int)
    transition_counts = defaultdict(lambda: defaultdict(int))

    ongoing = False
    start_frame = None
    onset_count = 0
    current_zones = set()
    start_zone = None

    for entry in rows:
        frame = entry["Frame"]
        onset = entry["Onset"]
        zones = _flatten_zones(entry["Zones"])

        if onset == "ON":
            if not ongoing:
                total_touches += 1
                ongoing = True
                start_frame = frame
                onset_count = 1
                current_zones = set(zones)
                start_zone = zones[0] if zones else "NN"
            else:
                onset_count += 1
                current_zones.update(zones)
        else:
            if ongoing and zones:
                current_zones.update(zones)

        if onset == "OFF":
            if ongoing:
                duration = frame - start_frame
                touch_durations.append(duration)
                onset_count_distribution[onset_count] += 1
                for z in current_zones:
                    zone_touch_count[z] += 1
                end_zone = zones[0] if zones else "NN"
                transition_counts[start_zone][end_zone] += 1
                ongoing = False
                start_frame = None
                onset_count = 0
                current_zones = set()
                start_zone = None

    if ongoing and rows:
        last_frame = rows[-1]["Frame"]
        duration = last_frame - start_frame
        touch_durations.append(duration)
        onset_count_distribution[onset_count] += 1
        for z in current_zones:
            zone_touch_count[z] += 1
        end_zone = start_zone if start_zone is not None else "NN"
        transition_counts[start_zone][end_zone] += 1

    return {
        "total_touches": total_touches,
        "touch_durations": touch_durations,
        "onset_count_distribution": dict(onset_count_distribution),
        "zone_touch_count": dict(zone_touch_count),
        "transition_counts": transition_counts,
    }


def _build_transition_matrix(transition_counts, zones):
    matrix = pd.DataFrame(0, index=zones, columns=zones)
    for start_zone, ends in transition_counts.items():
        if start_zone not in matrix.index:
            continue
        for end_zone, count in ends.items():
            if end_zone not in matrix.columns:
                continue
            matrix.at[start_zone, end_zone] += count
    return matrix.reindex(index=zones, columns=zones, fill_value=0)


def _plot_transition_heatmap(transition_df, zones, limb, output_folder):
    fig = px.imshow(
        transition_df,
        labels=dict(x="End Zone", y="Start Zone", color="Number of Touches"),
        x=zones,
        y=zones,
        color_continuous_scale="Blues",
        aspect="auto",
    )
    fig.update_traces(
        hovertemplate="Start Zone: %{y}<br>End Zone: %{x}<br>Number of Touches: %{z}<extra></extra>"
    )
    fig.update_layout(
        title=f"Touch Transition Heatmap {limb}",
        xaxis_title="End Zone",
        yaxis_title="Start Zone",
        coloraxis_colorbar=dict(title="Number of Touches"),
        height=1000,
        margin=dict(l=50, r=50, t=50, b=150),
    )
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(zones))), ticktext=zones, automargin=True)
    fig.write_html(os.path.join(output_folder, f"heatmap_{limb}.html"))


def _plot_touch_visualization_all_4(limb_rows, image_paths, output_folder):
    fig = make_subplots(
        rows=1,
        cols=4,
        subplot_titles=("Left Hand", "Right Hand", "Left Leg", "Right Leg"),
        horizontal_spacing=0.02,
    )

    img = Image.open(image_paths[0])
    img_width, img_height = img.size

    for i, limb in enumerate(LIMBS):
        data_rows = limb_rows[limb]
        image_path = image_paths[i]
        with open(image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode()

        x_coords = []
        y_coords = []
        colors = []
        sizes = []
        texts = []
        ongoing_touch = False

        for entry in data_rows:
            xs = entry.get("X", [])
            ys = entry.get("Y", [])
            onset = entry.get("Onset")
            frame = entry.get("Frame")
            zone = entry.get("Zones")

            points = list(zip(xs, ys)) if xs and ys else []
            if points:
                for idx, (x_raw, y_raw) in enumerate(points):
                    x = float(x_raw)
                    y = float(y_raw)
                    hover_text = (
                        f"Frame: {frame}<br>Point: {idx + 1}/{len(points)}<br>"
                        f"X: {x}<br>Y: {y}<br>Onset: {onset}<br>Zone: {zone}"
                    )
                    texts.append(hover_text)

                    if onset == "ON" and not ongoing_touch:
                        x_coords = [x]
                        y_coords = [y]
                        colors = ["green"]
                        sizes = [15]
                        ongoing_touch = True
                    elif onset == "ON" and ongoing_touch:
                        x_coords.append(x)
                        y_coords.append(y)
                        colors.append("black")
                        sizes.append(8)
                    elif onset == "OFF" and ongoing_touch:
                        x_coords.append(x)
                        y_coords.append(y)
                        is_last = idx == len(points) - 1
                        colors.append("red" if is_last else "black")
                        sizes.append(15 if is_last else 8)
                    elif onset == "OFF" and not ongoing_touch:
                        continue

            if onset == "OFF" and ongoing_touch:
                scatter = go.Scatter(
                    x=x_coords,
                    y=y_coords,
                    mode="markers+lines",
                    marker=dict(color=colors, size=sizes),
                    line=dict(color="black", width=2, dash="dot"),
                    name=f"Touch Path {i+1}",
                    text=texts,
                    hovertemplate="%{text}<extra></extra>",
                )
                fig.add_trace(scatter, row=1, col=i + 1)

                ongoing_touch = False
                texts = []

        axis_id = "" if i == 0 else str(i + 1)
        fig.add_layout_image(
            dict(
                source=f"data:image/png;base64,{encoded_image}",
                xref=f"x{axis_id}",
                yref=f"y{axis_id}",
                x=img_width / 2,
                y=img_height / 2,
                xanchor="center",
                yanchor="middle",
                sizex=img_width,
                sizey=img_height,
                sizing="contain",
                opacity=1,
                layer="below",
            ),
            row=1,
            col=i + 1,
        )

        fig.update_xaxes(
            visible=False,
            range=[0, img_width],
            autorange=False,
            fixedrange=False,
            row=1,
            col=i + 1,
        )
        fig.update_yaxes(
            visible=False,
            range=[0, img_height],
            autorange="reversed",
            fixedrange=False,
            scaleanchor="x",
            scaleratio=1,
            row=1,
            col=i + 1,
        )

    fig.update_layout(
        autosize=False,
        height=img_height + 200,
        width=img_width * 4,
        showlegend=False,
        margin=dict(l=0, r=0, t=50, b=50),
        dragmode="pan",
    )
    fig.write_html(os.path.join(output_folder, "touch_trajectory.html"), config={"scrollZoom": True})


def _write_analysis_tables(
    limbs,
    total_touches_list,
    touch_durations_list,
    total_duration_list,
    percentage_touching_list,
    average_touch_duration_list,
    touch_rate_list,
    stdev_list,
    total_frames,
    frame_rate,
    output_folder,
):
    data_frames = {
        "Limb": limbs,
        "Total Touches": total_touches_list,
        "Touch Durations [Frames]": touch_durations_list,
        "Total Duration [Frames]": total_duration_list,
        "Average Touch Duration [Frames]": average_touch_duration_list,
        "Percentage Touching": percentage_touching_list,
        "Touch Rate [Touches per 100 Frames]": touch_rate_list,
        "Standard Deviation [Frames]": stdev_list,
    }
    df_frames = pd.DataFrame(data_frames)

    df_frames.to_csv(os.path.join(output_folder, "analysis_table_frames.csv"), index=False)

    total_duration_seconds = [d / frame_rate for d in total_duration_list]
    touch_durations_seconds = [[d / frame_rate for d in durations] for durations in touch_durations_list]
    avg_durations_seconds = [d / frame_rate for d in average_touch_duration_list]
    stdev_seconds = [(d / frame_rate) if d is not None else None for d in stdev_list]

    if total_frames and frame_rate:
        percentage_touching_seconds = [
            (duration / (total_frames / frame_rate)) * 100 for duration in total_duration_seconds
        ]
    else:
        percentage_touching_seconds = [0 for _ in total_duration_seconds]

    if total_frames and frame_rate:
        touch_rate_per_min = [
            (touches / (total_frames / frame_rate)) * 60 for touches in total_touches_list
        ]
    else:
        touch_rate_per_min = [0 for _ in total_touches_list]

    df_seconds = pd.DataFrame(
        {
            "Limb": limbs,
            "Total Touches": total_touches_list,
            "Touch Durations [Seconds]": touch_durations_seconds,
            "Total Duration [Seconds]": total_duration_seconds,
            "Average Touch Duration [Seconds]": avg_durations_seconds,
            "Percentage Touching": percentage_touching_seconds,
            "Touch Rate [Touches per Minute]": touch_rate_per_min,
            "Standard Deviation [Seconds]": stdev_seconds,
        }
    )
    df_seconds.to_csv(os.path.join(output_folder, "analysis_table_seconds.csv"), index=False)

    return df_seconds


def _render_summary_table(df_seconds, total_frames, frame_rate, output_folder):
    data = df_seconds
    columns_to_display = [
        "Limb",
        "Total Touches",
        "Total Duration [Seconds]",
        "Average Touch Duration [Seconds]",
        "Standard Deviation [Seconds]",
        "Percentage Touching",
        "Touch Rate [Touches per Minute]",
    ]

    formatted_data = {
        k: [
            f"{int(v)}" if k == "Total Touches" and isinstance(v, (int, float)) else f"{v:.2f}"
            if isinstance(v, (int, float))
            else v
            for v in data[k]
        ]
        for k in columns_to_display
    }

    fig = go.Figure(
        data=[
            go.Table(
                header=dict(values=list(formatted_data.keys()), fill_color="paleturquoise", align="left"),
                cells=dict(values=[formatted_data[k] for k in formatted_data.keys()], fill_color="lavender", align="left"),
            )
        ]
    )
    total_seconds = (total_frames / frame_rate) if frame_rate else 0
    fig.update_layout(
        title=f"Touch Analysis Data (Length of video: {total_seconds:.2f} Seconds)",
        title_x=0.5,
        margin=dict(l=10, r=10, t=50, b=10),
        width=1400,
        height=700,
        font=dict(size=14),
    )
    fig.write_html(os.path.join(output_folder, "table.html"))


def _create_touch_length_histogram(onset_count_distribution_list, limbs, output_folder):
    all_keys = set()
    for d in onset_count_distribution_list:
        all_keys.update(d.keys())
    all_keys = sorted(all_keys)

    fig = go.Figure()
    for idx, d in enumerate(onset_count_distribution_list):
        fig.add_trace(
            go.Bar(
                x=list(all_keys),
                y=[d.get(key, 0) for key in all_keys],
                name=limbs[idx],
                hovertext=[
                    f"{limbs[idx]}<br>Length: {key}<br>Number of touches: {d.get(key, 0)}" for key in all_keys
                ],
                hoverinfo="text",
            )
        )
    fig.update_layout(
        barmode="stack",
        title="Touch length distribution",
        xaxis_title="Length of touch [number of onsets]",
        yaxis_title="Number of touches",
    )
    fig.write_html(os.path.join(output_folder, "histogram.html"))


def _create_touch_duration_histogram(touch_durations_list, frame_rate, limbs, output_folder):
    touch_sequence_list = []
    for touch_durations in touch_durations_list:
        duration_in_seconds = [math.ceil(d / frame_rate) for d in touch_durations]
        onset_count_distribution = dict(Counter(duration_in_seconds))
        touch_sequence_list.append(onset_count_distribution)

    all_keys = set()
    for d in touch_sequence_list:
        all_keys.update(d.keys())
    all_keys = sorted(all_keys)

    fig = go.Figure()
    for idx, d in enumerate(touch_sequence_list):
        fig.add_trace(
            go.Bar(
                x=list(all_keys),
                y=[d.get(key, 0) for key in all_keys],
                name=limbs[idx],
                hovertext=[
                    f"{limbs[idx]}<br>Length: {key} sec<br>Number of touches: {d.get(key, 0)}" for key in all_keys
                ],
                hoverinfo="text",
            )
        )
    fig.update_layout(
        barmode="stack",
        title="Touch Duration Distribution",
        xaxis_title="Touch Duration [second]",
        yaxis_title="Number of Touches",
        xaxis=dict(type="category"),
    )
    fig.write_html(os.path.join(output_folder, "histogram_2.html"))


def _read_new_template_flag():
    try:
        with open("config.json", "r") as f:
            cfg = json.load(f)
        return bool(cfg.get("new_template", False))
    except Exception:
        return False


def _zone_sort_key(zone: str):
    z = str(zone)
    special = z.startswith("BOX") or z in {"OUTSIDE", "LINE", "NN"}
    return (1 if special else 0, len(z), z)


def _get_zone_list(new_template: bool):
    zones_dir = "icons/zones3_new_template" if new_template else "icons/zones3"
    zones = []
    try:
        for filename in os.listdir(zones_dir):
            if filename.lower().endswith((".png", ".jpg", ".jpeg")):
                zones.append(os.path.splitext(filename)[0])
    except Exception:
        zones = []
    if "NN" not in zones:
        zones.append("NN")
    return sorted(zones, key=_zone_sort_key)


def do_analysis(folder_path, output_folder, name, debug, frame_rate):
    if frame_rate is None:
        print("Analysis error: Frame rate is None.")
        return 0

    export_dir = os.path.dirname(folder_path.rstrip(os.sep))
    export_path = os.path.join(export_dir, "export", f"{name}_export.csv")
    os.makedirs(output_folder, exist_ok=True)

    limb_rows, total_frames = _load_limb_rows(export_path)
    if debug:
        print("Total frames:", total_frames)

    total_touches_list = []
    touch_durations_list = []
    total_duration_list = []
    percentage_touching_list = []
    average_touch_duration_list = []
    touch_rate_list = []
    onset_count_distribution_list = []
    zone_touch_count_list = []
    stdev_list = []
    transition_matrices = []

    new_template = _read_new_template_flag()
    zones_default = _get_zone_list(new_template)

    for limb in LIMBS:
        metrics = _compute_limb_metrics(limb_rows[limb])
        total_touches = metrics["total_touches"]
        touch_durations = metrics["touch_durations"]
        onset_count_distribution = metrics["onset_count_distribution"]
        zone_touch_count = metrics["zone_touch_count"]
        transition_counts = metrics["transition_counts"]

        total_touches_list.append(total_touches)
        touch_durations_list.append(touch_durations)
        total_duration = sum(touch_durations)
        total_duration_list.append(total_duration)

        percentage_touching = (total_duration / total_frames) * 100 if total_frames else 0
        percentage_touching_list.append(percentage_touching)

        average_touch_duration = total_duration / len(touch_durations) if touch_durations else 0
        average_touch_duration_list.append(average_touch_duration)

        touch_rate = (total_touches / total_frames) * 100 if total_frames else 0
        touch_rate_list.append(touch_rate)

        stdev_list.append(statistics.stdev(touch_durations) if len(touch_durations) >= 2 else None)
        onset_count_distribution_list.append(onset_count_distribution)
        zone_touch_count_list.append(zone_touch_count)

        transition_keys = set(metrics["transition_counts"].keys())
        for ends in metrics["transition_counts"].values():
            transition_keys.update(ends.keys())
        zones = sorted(set(zones_default) | set(zone_touch_count.keys()) | transition_keys, key=_zone_sort_key)
        transition_df = _build_transition_matrix(transition_counts, zones)
        transition_matrices.append((transition_df, zones))

    for limb, (transition_df, zones) in zip(LIMBS, transition_matrices):
        _plot_transition_heatmap(transition_df, zones, limb, output_folder)

    if new_template:
        image_paths = [
            "icons/LH_new_template.png",
            "icons/RH_new_template.png",
            "icons/LL_new_template.png",
            "icons/RL_new_template.png",
        ]
    else:
        image_paths = [
            "icons/LH.png",
            "icons/RH.png",
            "icons/LL.png",
            "icons/RL.png",
        ]
    _plot_touch_visualization_all_4(limb_rows, image_paths, output_folder)

    df_seconds = _write_analysis_tables(
        limbs=LIMBS,
        total_touches_list=total_touches_list,
        touch_durations_list=touch_durations_list,
        total_duration_list=total_duration_list,
        percentage_touching_list=percentage_touching_list,
        average_touch_duration_list=average_touch_duration_list,
        touch_rate_list=touch_rate_list,
        stdev_list=stdev_list,
        total_frames=total_frames,
        frame_rate=frame_rate,
        output_folder=output_folder,
    )

    _render_summary_table(df_seconds, total_frames, frame_rate, output_folder)
    _create_touch_length_histogram(onset_count_distribution_list, LIMBS, output_folder)
    _create_touch_duration_histogram(touch_durations_list, frame_rate, LIMBS, output_folder)

    graphs = [
        "touch_trajectory.html",
        "table.html",
        "histogram.html",
        "histogram_2.html",
        "heatmap_LH.html",
        "heatmap_RH.html",
        "heatmap_LL.html",
        "heatmap_RL.html",
    ]

    html_content = f"""
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <meta charset=\"UTF-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
        <title>{name}</title>
        <style>
            body {{
                text-align: center;
                font-family: Arial, sans-serif;
            }}
            iframe {{
                width: 90%;
                border: none;
                display: block;
                margin: 0 auto;
            }}
            h2 {{
                text-align: center;
            }}
            .row {{
                display: flex;
                gap: 16px;
                justify-content: center;
                align-items: stretch;
                flex-wrap: wrap;
                margin: 0 auto;
                width: 95%;
            }}
            .half {{
                flex: 1 1 45%;
                min-width: 420px;
            }}
            .half iframe {{
                width: 100%;
            }}
        </style>
    </head>
    <body>
        <h1>{name}</h1>
    """

    for graph in graphs:
        if graph in ("histogram.html", "histogram_2.html"):
            continue
        height = "1200px" if "touch_trajectory" in graph else "800px"
        html_content += f"""
        <h2>{graph}</h2>
        <iframe src=\"{graph}\" style=\"height: {height};\"></iframe>
        """

    html_content += """
        <h2>Histograms</h2>
        <div class=\"row\">
            <div class=\"half\">
                <iframe src=\"histogram.html\" style=\"height: 700px;\"></iframe>
            </div>
            <div class=\"half\">
                <iframe src=\"histogram_2.html\" style=\"height: 700px;\"></iframe>
            </div>
        </div>
    """

    html_content += """
    </body>
    </html>
    """

    file_path = os.path.join(output_folder, f"master_{name}.html")
    with open(file_path, "w") as f:
        f.write(html_content)

    webbrowser.open(file_path)


if __name__ == "__main__":
    data_path = "Labeled_data/test/data/"
    output_folder = "Labeled_data/test/plots/"
    name = "test"
    debug = False
    do_analysis(data_path, output_folder, name, debug, frame_rate=30)
