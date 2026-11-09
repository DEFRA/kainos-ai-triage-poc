# Spatial screening: to fix
1. Define the coordinate contract as British National Grid (BNG, EPSG:27700), with each point ordered as `[easting, northing]` in metres, matching the LPA client. The internal IRZ client must use these coordinates directly rather than interpret or transform them as longitude/latitude. Reject malformed coordinates and values that are clearly not plausible BNG coordinates. The current spatial request remains a single GeoJSON Polygon ring; MultiPolygon support is out of scope for this fix.
2. Bound the internal IRZ request with a positive `request_timeout_seconds` setting in `environment/configs/spatial_screening.yaml`.
3. Parse and validate the model's IRZ answer. For the code-table implementation, `value` must be one of the mapped options for the named category and `text` must match that value. Value `0` is the valid "Not applicable" option; "Unknown" and unoffered values are invalid. The orchestrator derives `human_review_required` from the configurable `human_review_confidence_threshold` in `environment/configs/spatial_screening.yaml` (High by default; Medium is also supported).
4. The output doesn't include internal_link. It's the main evidence for the screening, so keep it alongside the polygon and the answers.
5. Consider reworking the IRZ questions based on the following information:
    - The "questions" are not really questions, they are types of developments.
    - Most the information could potentially be extractable from the IRZ codes, no need to parse HTML, the IRZ code field meaning could be as below: requires conformation

        ```irzcode=2000864,0300000500000,4,```

        ```
            {
            "site_or_designation_id": "2000864",
            "consultation_threshold_code": "0300000500000",
            "irz_zone_selector": "4",
            "advice_reference": null
            }
        ```
    - The prompt may need reworking depending on what we do.
    - The strategic-solutions match can hit the coordinates. check_strategic does gis_ref in url, and the URL contains location=521707.989,444919.962. Any 5-digit reference that also appears in those digits would match falsely. Matching only against the irzcode part of the URL fixes it.
6. Additional geospatial checks use the reusable `intersect_polygon_with_layer` utility. The code-table implementation checks Built Up Areas coverage, plus binary intersections with Best and Most Versatile Land and Protected Landscape Impact Risk Zones.
7. Rather than XML parsing the IRZ page, I now have some guidance on an alternative way to retrieve the development type options, formally referred to as "IRZ questions":
    - IRZ internal link will retrieve a url eg:
        - https://irz.geodata.org.uk/IRZ_internal/IRZ.php?location=521708,444920&point_txt=%20(API%20geometry%20centre)&irzcode=1002380,0321000432040,3,;1005735,0300000500000,5,;UK9006171,0000000000000,B1,21103
        - https://irz.geodata.org.uk/IRZ_internal/IRZ.php?location=520045,445317&point_txt=%20(API%20geometry%20centre)&irzcode=1005735,0300000000000,7,;1002380,0111344322342,2,;UK9006171,0000000000000,B1,21103
    - The query parameter is `irzcode` (singular). Its decoded value contains semicolon-separated records, each with a site/designation ID, a 13-digit development code, a zone selector, and an optional advice reference. Parse the URL query structurally and validate each record.
    - The 13 digits correspond by position to the 13 categories in `data/irz_code_table.csv`. Each position is `0` (no trigger) or an option number from 1 to 6.
    - Multiple records contribute together: union the distinct positive option values for each category; do not let one record replace another. Always include option value `0` with text "Not applicable".
    - Keep the parsed source records under a separate `irz_codes` output key. The compact LLM mapping should contain category names and option objects with only `value` and `text`; avoid repeating source records under every option.
    - Match strategic advice references exactly from records whose development code is all zeroes, rather than searching the whole URL. Retain the matching paragraphs in the stage result.
    - Add a separate code-table implementation under `spatial_screening/implementations`, retaining the existing HTML-based implementation and its HTML parsing helpers.
    - Provide the mapped categories and consultation context to the LLM. Each answer should include `category`, numeric `value`, matching `text`, confidence, reasoning, supporting and conflicting evidence, and missing information. Value and text must match an offered option. The orchestrator derives review status from confidence (Medium or Low requires review); the LLM does not emit a review flag.
    - Return the polygon, internal link, raw IRZ code records, category mapping, strategic-solution matches, validated answers, and review status together as the stage result. Saving it as a stage file is tracked separately in item 9.
8. Submitting the answers back to the IRZ tool and reading the final advice still needs implementing, can this be done without the HTML parsing? Requires investigation.
9. The stage output is only being returned, not saved, make sure we add a path field in the spatial agent to save the output json as a stage output.
10. The stage output json is not well designed, requires rethinking and planing.
11. Keep spatial orchestrators under `spatial_screening/implementations`: the existing HTML-based implementation and the additional code-table implementation are separate, and no agent module remains at the spatial-screening package root.
12. Confirm permission to use the internal IRZ endpoint. My own rules doc notes that internal SSSI IRZs are "INTERNAL USE ONLY". Even though the API came from someone at Natural England, get written confirmation that the PoC can use it, ideally by email. (this is NOT an action for github copilot).
13. Classify the development as urban or rural by measuring its percentage coverage by the union of features in the `os_open_built_up_areas` layer of the OS Open Built Up Areas GeoPackage. Use this Built Up Areas layer, not the Built Up Extents or Non Built Up Extents layers. A configurable `urban_built_up_coverage_threshold_percent` in `environment/configs/spatial_screening.yaml` determines the classification. Pass the classification into the IRZ model context and retain the classification, coverage percentage, threshold, and source layer in stage output.
14. Check the development polygon against the Best and Most Versatile Land and Protected Landscape Impact Risk Zones layers before the IRZ request. Any geometric intersection counts as `true`. Include each boolean and source layer in the stage output; do not add these checks to the LLM context.
