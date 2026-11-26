import ee


def empty_image_with_bands(bands):
    if not bands:
        raise ValueError('bands must not be empty')
    return ee.Image.constant([0] * len(bands)).rename(list(bands)).updateMask(0)


def build_landsat_image(geom, dates, vis, collection, composite_mode='BEST_IMAGE'):
    composite_mode = composite_mode or 'BEST_IMAGE'

    def scale(image):
        return image.addBands(image.select('SR_B.').multiply(0.0000275).add(-0.2), None, True)

    def mask_clouds(image):
        qa = image.select('QA_PIXEL')
        mask = qa.bitwiseAnd(1 << 3).eq(0).And(qa.bitwiseAnd(1 << 4).eq(0))
        return image.updateMask(mask)

    if composite_mode == 'BEST_IMAGE':
        images = (ee.ImageCollection(collection)
                  .filterDate(dates['dtStart'], dates['dtEnd'])
                  .filterBounds(geom)
                  .filter(ee.Filter.lt('CLOUD_COVER', 40))
                  .sort('CLOUD_COVER_LAND')
                  .sort('CLOUD_COVER', False))
        size = images.size()
        required_bands = vis['bands']

        def has_required_bands(image):
            band_names = image.bandNames()
            has_all = ee.List(required_bands).map(
                lambda band: band_names.contains(band)
            ).reduce(ee.Reducer.min())
            return image.set('has_required_bands', has_all)

        valid_images = images.map(has_required_bands).filter(ee.Filter.eq('has_required_bands', 1))
        valid_size = valid_images.size()
        best = scale(valid_images.first()).select(required_bands)
        fallback = images.map(scale).map(
            lambda image: ee.Algorithms.If(
                ee.List(required_bands).map(
                    lambda band: image.bandNames().contains(band)
                ).reduce(ee.Reducer.min()),
                image.select(required_bands),
                empty_image_with_bands(required_bands),
            )
        ).mosaic()
        return ee.Algorithms.If(
            size.gt(0),
            ee.Algorithms.If(valid_size.gt(0), best, fallback),
            empty_image_with_bands(required_bands),
        )

    images = (ee.ImageCollection(collection)
              .filterDate(dates['dtStart'], dates['dtEnd'])
              .filterBounds(geom))
    size = images.size()
    band_names = images.first().bandNames()
    available_bands = band_names.filter(ee.Filter.inList('item', vis['bands']))
    processed = ee.Algorithms.If(
        available_bands.size().eq(0),
        empty_image_with_bands(vis['bands']),
        images.map(mask_clouds).map(scale).select(available_bands).mosaic(),
    )
    return ee.Algorithms.If(size.gt(0), processed, empty_image_with_bands(vis['bands']))
