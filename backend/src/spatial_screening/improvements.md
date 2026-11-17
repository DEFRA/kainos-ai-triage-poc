# Spatial screening: to fix

1. Confirm permission to use the internal IRZ endpoint. My own rules doc notes that internal SSSI IRZs are "INTERNAL USE ONLY". Even though the API came from someone at Natural England, get written confirmation that the PoC can use it, ideally by email. (this is NOT an action for github copilot).
2. Rework some of the logic for the geospatial checks based on the rules to answer the checklist questions. 
    - If we need to know the overlap intersection area, we will return something like this

        ```
        {
        "best_and_most_versatile_land": {
            "intersects": true,
            "overlap_area_hectares": 23.4
        }
        }
        ```
    - For best and most versatile lands, the check needs to change, it is no longer just checking if the polygon intersects with any layer, it should be only if intersects with features that are under category CAT2 == 1 or 2 or 3. The intersect overlap area should also only be with those features.
 