#!/usr/bin/env python3
"""
Script to migrate VISPARAMS from code to MongoDB
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path to import app modules
sys.path.append(str(Path(__file__).parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.models.vis_params import (
    VisParamDocument, BandConfig, VisParam, 
    SatelliteVisParam, LandsatCollectionMapping,
    SentinelCollectionMapping
)
from app.visualization.visParam import VISPARAMS


async def migrate_vis_params():
    """Migrate VISPARAMS to MongoDB with improved structure"""
    
    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.get("MONGODB_URL", "mongodb://localhost:27017"))
    db = client[settings.get("MONGODB_DB", "tvi")]
    collection = db.vis_params
    
    print("Starting migration of visualization parameters to MongoDB...")
    
    # Clear existing data (optional - comment out to preserve)
    # await collection.delete_many({})
    
    documents = []
    
    # Process each visualization type
    for vis_name, config in VISPARAMS.items():
        print(f"Processing {vis_name}...")
        
        # Determine category and create document
        if vis_name.startswith('landsat'):
            # Landsat configurations with multiple satellites
            category = 'landsat'
            display_name = vis_name.replace('-', ' ').title()
            
            # Extract satellite configs
            satellite_configs = []
            for collection_id, params in config['visparam'].items():
                satellite_configs.append(SatelliteVisParam(
                    collection_id=collection_id,
                    vis_params=VisParam(**params)
                ))
            
            doc = VisParamDocument(
                _id=vis_name,
                name=vis_name,
                display_name=display_name,
                description=f"Landsat visualization parameters for {display_name}",
                category=category,
                satellite_configs=satellite_configs,
                tags=['landsat', 'multispectral']
            )
            
        else:
            # Sentinel-2 style configurations
            category = 'sentinel2'
            display_name = vis_name.replace('-', ' ').title()
            
            # Process band config if present
            band_config = None
            if 'select' in config:
                select_data = config['select']
                if isinstance(select_data, tuple) and len(select_data) == 2:
                    band_config = BandConfig(
                        original_bands=select_data[0],
                        mapped_bands=select_data[1]
                    )
                elif isinstance(select_data, list):
                    band_config = BandConfig(
                        original_bands=select_data,
                        mapped_bands=None
                    )
            
            # Create document
            doc = VisParamDocument(
                _id=vis_name,
                name=vis_name,
                display_name=display_name,
                description=f"Sentinel-2 visualization parameters for {display_name}",
                category=category,
                band_config=band_config,
                vis_params=VisParam(**config['visparam']),
                tags=['sentinel2', 'multispectral']
            )
        
        documents.append(doc.model_dump(by_alias=True))
    
    # Insert all documents
    if documents:
        result = await collection.insert_many(documents, ordered=False)
        print(f"Inserted {len(result.inserted_ids)} visualization parameter documents")
    
    # Also migrate Landsat collection mappings
    landsat_mappings = LandsatCollectionMapping(
        _id="landsat_collections",
        mappings=[
            {
                "start_year": 1985,
                "end_year": 2011,
                "collection": "LANDSAT/LT05/C02/T1_L2",
                "satellite": "Landsat 5"
            },
            {
                "start_year": 2012,
                "end_year": 2013,
                "collection": "LANDSAT/LE07/C02/T1_L2",
                "satellite": "Landsat 7"
            },
            {
                "start_year": 2014,
                "end_year": 2024,
                "collection": "LANDSAT/LC08/C02/T1_L2",
                "satellite": "Landsat 8"
            },
            {
                "start_year": 2025,
                "end_year": 2030,
                "collection": "LANDSAT/LC09/C02/T1_L2",
                "satellite": "Landsat 9"
            }
        ]
    )
    
    await collection.replace_one(
        {"_id": "landsat_collections"},
        landsat_mappings.model_dump(by_alias=True),
        upsert=True
    )
    print("Landsat collection mappings migrated")
    
    # Also migrate Sentinel-2 collection configurations
    sentinel_config = {
        "_id": "sentinel_collections",
        "collections": [
            {
                "name": "COPERNICUS/S2_HARMONIZED",
                "display_name": "Sentinel-2 Harmonized",
                "description": "Harmonized Sentinel-2 MSI: MultiSpectral Instrument, Level-2A",
                "start_date": "2015-06-27",
                "end_date": None,
                "bands": {
                    "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
                    "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
                    "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
                    "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
                    "B5": {"name": "B5", "description": "Red Edge 1", "wavelength": "705nm", "resolution": "20m"},
                    "B6": {"name": "B6", "description": "Red Edge 2", "wavelength": "740nm", "resolution": "20m"},
                    "B7": {"name": "B7", "description": "Red Edge 3", "wavelength": "783nm", "resolution": "20m"},
                    "B8": {"name": "B8", "description": "NIR", "wavelength": "842nm", "resolution": "10m"},
                    "B8A": {"name": "B8A", "description": "Red Edge 4", "wavelength": "865nm", "resolution": "20m"},
                    "B9": {"name": "B9", "description": "Water Vapor", "wavelength": "945nm", "resolution": "60m"},
                    "B10": {"name": "B10", "description": "Cirrus", "wavelength": "1375nm", "resolution": "60m"},
                    "B11": {"name": "B11", "description": "SWIR 1", "wavelength": "1610nm", "resolution": "20m"},
                    "B12": {"name": "B12", "description": "SWIR 2", "wavelength": "2190nm", "resolution": "20m"},
                    "QA10": {"name": "QA10", "description": "Cloud mask (10m)", "resolution": "10m"},
                    "QA20": {"name": "QA20", "description": "Cloud mask (20m)", "resolution": "20m"},
                    "QA60": {"name": "QA60", "description": "Cloud mask (60m)", "resolution": "60m"}
                },
                "quality_bands": ["QA10", "QA20", "QA60"],
                "metadata_properties": [
                    "CLOUDY_PIXEL_PERCENTAGE", "CLOUD_COVERAGE_ASSESSMENT", "DATASTRIP_ID",
                    "DATATAKE_IDENTIFIER", "GENERATION_TIME", "GRANULE_ID",
                    "MEAN_INCIDENCE_AZIMUTH_ANGLE", "MEAN_INCIDENCE_ZENITH_ANGLE",
                    "MEAN_SOLAR_AZIMUTH_ANGLE", "MEAN_SOLAR_ZENITH_ANGLE", "MGRS_TILE",
                    "PROCESSING_BASELINE", "PRODUCT_ID", "SENSING_ORBIT_DIRECTION",
                    "SENSING_ORBIT_NUMBER", "SOLAR_IRRADIANCE"
                ]
            },
            {
                "name": "COPERNICUS/S2_SR_HARMONIZED",
                "display_name": "Sentinel-2 SR Harmonized",
                "description": "Harmonized Sentinel-2 Surface Reflectance",
                "start_date": "2017-03-28",
                "end_date": None,
                "bands": {
                    "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
                    "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
                    "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
                    "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
                    "B5": {"name": "B5", "description": "Red Edge 1", "wavelength": "705nm", "resolution": "20m"},
                    "B6": {"name": "B6", "description": "Red Edge 2", "wavelength": "740nm", "resolution": "20m"},
                    "B7": {"name": "B7", "description": "Red Edge 3", "wavelength": "783nm", "resolution": "20m"},
                    "B8": {"name": "B8", "description": "NIR", "wavelength": "842nm", "resolution": "10m"},
                    "B8A": {"name": "B8A", "description": "Red Edge 4", "wavelength": "865nm", "resolution": "20m"},
                    "B9": {"name": "B9", "description": "Water Vapor", "wavelength": "945nm", "resolution": "60m"},
                    "B11": {"name": "B11", "description": "SWIR 1", "wavelength": "1610nm", "resolution": "20m"},
                    "B12": {"name": "B12", "description": "SWIR 2", "wavelength": "2190nm", "resolution": "20m"},
                    "SCL": {"name": "SCL", "description": "Scene Classification Map", "resolution": "20m"},
                    "MSK_CLDPRB": {"name": "MSK_CLDPRB", "description": "Cloud Probability", "resolution": "20m"},
                    "MSK_SNWPRB": {"name": "MSK_SNWPRB", "description": "Snow Probability", "resolution": "20m"}
                },
                "quality_bands": ["SCL", "MSK_CLDPRB", "MSK_SNWPRB"]
            }
        ],
        "default_collection": "COPERNICUS/S2_HARMONIZED",
        "cloud_filter_params": {
            "max_cloud_coverage": 20,
            "use_cloud_score": True,
            "cloud_score_threshold": 0.5,
            "use_qa_band": True,
            "qa_band": "QA60",
            "cloud_bit": 10,
            "cirrus_bit": 11
        }
    }
    
    await collection.replace_one(
        {"_id": "sentinel_collections"},
        sentinel_config,
        upsert=True
    )
    print("Sentinel-2 collection configurations migrated")
    
    # Create indexes
    await collection.create_index("name")
    await collection.create_index("category")
    await collection.create_index("active")
    await collection.create_index("tags")
    print("Indexes created")
    
    print("Migration completed successfully!")
    
    # Display summary
    count = await collection.count_documents({})
    print(f"\nTotal documents in vis_params collection: {count}")
    
    # Show sample document
    sample = await collection.find_one({"name": "tvi-green"})
    if sample:
        print("\nSample document (tvi-green):")
        import json
        print(json.dumps(sample, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(migrate_vis_params())