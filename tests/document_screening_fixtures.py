def valid_document_findings():
    field = {
        "value": None,
        "status": "not_stated",
        "confidence": None,
        "reason": "Not stated in the supplied documents.",
        "evidence": [],
    }
    signal_names = (
        "eia_screening",
        "eia_scoping",
        "amendment",
        "conditions",
        "pre_application",
        "prior_approval",
        "minerals_waste_existing",
        "minerals_waste_new",
    )
    development_fields = (
        "all_consultations",
        "infrastructure",
        "wind_solar",
        "minerals_oil_gas",
        "rural_non_residential",
        "residential",
        "rural_residential",
        "air_pollution",
        "combustion",
        "waste",
        "compost",
        "discharge",
        "water_supply",
    )
    return {
        "consultation_type_signals": {
            name: {
                "classification": "no_signal",
                "confidence": "high",
                "reason": "No supporting signal was found.",
                "evidence": [],
            }
            for name in signal_names
        },
        "location": {
            "coordinates": {
                "easting": 500000,
                "northing": 200000,
                "grid_reference": None,
                "units": None,
                "crs": None,
                "confidence": "high",
                "evidence": [],
            },
            "site_address": dict(field),
            "site_area": {
                "value": "4 ha",
                "status": "stated",
                "confidence": "high",
                "reason": "Site area is explicitly stated.",
                "evidence": [{
                    "document_id": "123",
                    "page": 1,
                    "quote": "Site area: 4 ha",
                }],
            },
        },
        "site_and_surroundings": {
            "existing_use": dict(field),
            "greenfield_or_brownfield": dict(field),
            "features_affected": [],
            "designated_sites_mentioned": [],
        },
        "scale_and_type_of_development": {
            name: dict(field) for name in development_fields
        },
        "agricultural_land": {
            "agriculture": dict(field),
            "agricultural_land_area_lost": dict(field),
            "alc_grades": dict(field),
            "soil_survey": dict(field),
        },
        "assessments": [],
        "limitations": [],
        "notes": [],
    }
