import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fiona
from shapely.geometry import Polygon, mapping

from backend.src.spatial_screening.tools.geospatial import (
	intersect_polygon_with_layer,
)


class GeospatialIntersectionTests(unittest.TestCase):
	def _create_geopackage(self, path: Path) -> None:
		schema = {"geometry": "Polygon", "properties": {"name": "str"}}
		with fiona.open(
			path,
			"w",
			driver="GPKG",
			layer="regions",
			schema=schema,
			crs="EPSG:27700",
		) as layer:
			for name, bounds in (
				("west", (0, 0, 6, 10)),
				("east", (4, 0, 10, 10)),
			):
				layer.write({
					"geometry": mapping(Polygon.from_bounds(*bounds)),
					"properties": {"name": name},
				})

	def _create_invalid_geopackage(self, path: Path) -> None:
		schema = {"geometry": "Polygon", "properties": {"name": "str"}}
		with fiona.open(
			path,
			"w",
			driver="GPKG",
			layer="regions",
			schema=schema,
			crs="EPSG:27700",
		) as layer:
			layer.write({
				"geometry": mapping(Polygon([
					(0, 0), (10, 10), (0, 10), (10, 0), (0, 0),
				])),
				"properties": {"name": "self-crossing"},
			})

	def test_uses_union_area_when_polygon_overlaps_multiple_regions(self):
		with TemporaryDirectory() as directory:
			data_source = Path(directory) / "regions.gpkg"
			self._create_geopackage(data_source)

			result = intersect_polygon_with_layer(
				data_source,
				"regions",
				[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
			)

		self.assertTrue(result["intersects"])
		self.assertEqual(result["polygon_area_m2"], 100.0)
		self.assertEqual(result["intersection_area_m2"], 100.0)
		self.assertEqual(result["intersection_percentage"], 100.0)
		self.assertEqual(result["intersection_geometry"]["type"], "Polygon")
		self.assertEqual(len(result["intersecting_regions"]), 2)
		self.assertEqual(
			sum(
				region["intersection_area_m2"]
				for region in result["intersecting_regions"]
			),
			120.0,
		)
		self.assertEqual(result["repaired_geometries"], [])
		json.dumps(result)

	def test_category_filter_limits_union_area_and_preserves_boundary_contact(self):
		with TemporaryDirectory() as directory:
			data_source = Path(directory) / "categories.gpkg"
			with fiona.open(
				data_source, "w", driver="GPKG", layer="regions",
				schema={"geometry": "Polygon", "properties": {"CAT2": "int"}},
				crs="EPSG:27700",
			) as layer:
				for category, bounds in (
					(1, (0, 0, 60, 100)),
					(2, (40, 0, 80, 100)),
					(3, (70, 0, 90, 100)),
					(4, (0, 0, 100, 100)),
					(5, (0, 0, 100, 100)),
				):
					layer.write({
						"geometry": mapping(Polygon.from_bounds(*bounds)),
						"properties": {"CAT2": category},
					})
			for bounds, intersects, area, feature_count in (
				((0, 0, 100, 100), True, 9000.0, 3),
				((90, 0, 100, 100), True, 0.0, 1),
				((95, 0, 100, 100), False, 0.0, 0),
			):
				with self.subTest(bounds=bounds):
					result = intersect_polygon_with_layer(
						data_source, "regions",
						[list(point) for point in Polygon.from_bounds(*bounds).exterior.coords],
						where="CAT2 IN (1, 2, 3)",
					)
					self.assertEqual(result["intersects"], intersects)
					self.assertEqual(result["intersection_area_m2"], area)
					self.assertEqual(len(result["intersecting_regions"]), feature_count)

	def test_repairs_and_reports_invalid_candidate_geometry(self):
		with TemporaryDirectory() as directory:
			data_source = Path(directory) / "invalid.gpkg"
			self._create_invalid_geopackage(data_source)

			result = intersect_polygon_with_layer(
				data_source,
				"regions",
				[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
			)

		self.assertTrue(result["intersects"])
		self.assertEqual(result["intersection_percentage"], 50.0)
		self.assertEqual(len(result["repaired_geometries"]), 1)
		self.assertIn(
			"Self-intersection",
			result["repaired_geometries"][0]["reason"],
		)

	def test_rejects_unknown_layer(self):
		with TemporaryDirectory() as directory:
			data_source = Path(directory) / "regions.gpkg"
			self._create_geopackage(data_source)

			with self.assertRaisesRegex(ValueError, "was not found"):
				intersect_polygon_with_layer(
					data_source,
					"missing",
					[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
				)


if __name__ == "__main__":
	unittest.main()