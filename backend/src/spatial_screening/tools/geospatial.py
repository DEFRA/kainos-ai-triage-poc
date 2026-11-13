"""Read geospatial layers and measure their overlap with a development polygon."""

import json
import math
from pathlib import Path
from typing import Union

import fiona
from pyproj import CRS, Transformer
from shapely import is_valid_reason, make_valid
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import transform, unary_union


def intersect_polygon_with_layer(
    data_source: Union[str, Path],
    layer_name: str,
    polygon: list[list[float]],
    polygon_crs: str = "EPSG:27700",
    *,
    where: str | None = None,
) -> dict[str, object]:
    """Return JSON-ready intersection details and unique polygon coverage.

    The data source may be a GeoPackage file or a File Geodatabase directory.
    Area is reported in square metres; the polygon CRS must be projected.
    Optional where is an OGR SQL attribute filter applied before intersection.
    """
    data_source = Path(data_source).resolve()
    if not data_source.exists():
        raise FileNotFoundError(f"Geospatial data source does not exist: {data_source}")
    if not isinstance(layer_name, str) or not layer_name.strip():
        raise ValueError("layer_name must be a non-empty string.")

    target_crs = CRS.from_user_input(polygon_crs)
    if not target_crs.is_projected or len(target_crs.axis_info) < 2:
        raise ValueError("polygon_crs must be a projected CRS with two axes.")
    unit_factors = [
        axis.unit_conversion_factor for axis in target_crs.axis_info[:2]
    ]
    if any(factor is None or not math.isfinite(factor) or factor <= 0 for factor in unit_factors):
        raise ValueError("polygon_crs must have known linear units.")
    area_to_square_metres = unit_factors[0] * unit_factors[1]

    available_layers = fiona.listlayers(str(data_source))
    if layer_name not in available_layers:
        raise ValueError(
            f"Layer {layer_name!r} was not found in {data_source}; "
            f"available layers: {', '.join(available_layers)}."
        )

    development_polygon = Polygon(polygon)
    if development_polygon.is_empty or not development_polygon.is_valid:
        raise ValueError("polygon must be a non-empty, valid polygon ring.")
    polygon_area = development_polygon.area
    if not math.isfinite(polygon_area) or polygon_area <= 0:
        raise ValueError("polygon must have a positive finite area.")

    feature_intersections = []
    clipped_geometries = []
    repaired_geometries = []
    with fiona.open(str(data_source), layer=layer_name) as layer:
        layer_crs_input = layer.crs_wkt or layer.crs
        if not layer_crs_input:
            raise ValueError(f"Layer {layer_name!r} has no declared CRS.")
        layer_crs = CRS.from_user_input(layer_crs_input)
        if layer_crs != target_crs:
            layer_to_polygon = Transformer.from_crs(
                layer_crs, target_crs, always_xy=True
            )
            polygon_to_layer = Transformer.from_crs(
                target_crs, layer_crs, always_xy=True
            )
            layer_bounds = polygon_to_layer.transform_bounds(
                *development_polygon.bounds
            )
            project_to_polygon = layer_to_polygon.transform
        else:
            layer_bounds = development_polygon.bounds
            project_to_polygon = None

        for feature in layer.filter(bbox=layer_bounds, where=where):
            if feature.geometry is None:
                continue
            region = shape(feature.geometry)
            if region.is_empty:
                continue
            repair_reason = None
            if not region.is_valid:
                repair_reason = is_valid_reason(region)
                region = make_valid(region)
                if not region.is_valid:
                    raise ValueError(
                        f"Feature {feature.id!r} in layer {layer_name!r} remains "
                        "invalid after repair."
                    )
                repaired_geometries.append({
                    "feature_id": str(feature.id),
                    "reason": repair_reason,
                })
            if project_to_polygon is not None:
                region = transform(project_to_polygon, region)
            if not region.intersects(development_polygon):
                continue

            intersection = region.intersection(development_polygon)
            feature_intersections.append({
                "feature_id": str(feature.id),
                "intersection_area_m2": round(
                    intersection.area * area_to_square_metres, 3
                ),
            })
            if not intersection.is_empty:
                clipped_geometries.append(intersection)

    unique_intersection = unary_union(clipped_geometries) if clipped_geometries else None
    unique_intersection_area = unique_intersection.area if unique_intersection is not None else 0.0
    intersection_geometry = (
        json.loads(json.dumps(mapping(unique_intersection)))
        if unique_intersection is not None
        else None
    )
    intersection_percentage = min(
        100.0,
        unique_intersection_area / polygon_area * 100,
    )

    return {
        "data_source": str(data_source),
        "layer_name": layer_name,
        "polygon_crs": target_crs.to_string(),
        "intersects": bool(feature_intersections),
        "polygon_area_m2": round(polygon_area * area_to_square_metres, 3),
        "intersection_area_m2": round(
            unique_intersection_area * area_to_square_metres, 3
        ),
        "intersection_percentage": round(intersection_percentage, 6),
        "intersection_geometry": intersection_geometry,
        "intersecting_regions": feature_intersections,
        "repaired_geometries": repaired_geometries,
    }