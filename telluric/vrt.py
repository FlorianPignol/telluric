"""borrowed from Rasterio"""

import xml.etree.ElementTree as ET

from rasterio.enums import MaskFlags
from rasterio.crs import CRS
from rasterio.windows import from_bounds, Window
import rasterio
import os
from telluric.base_vrt import BaseVRT


def find_and_convert_to_type(_type, node, path):
    value = node.find(path)
    if value is not None:
        value = _type(value.text)
    return value


def wms_vrt(wms_file, bounds=None, resolution=None):
    from telluric import rasterization, constants
    wms_tree = ET.parse(wms_file)
    left = find_and_convert_to_type(float, wms_tree, ".//DataWindow/UpperLeftX")
    up = find_and_convert_to_type(float, wms_tree, ".//DataWindow/UpperLeftY")
    right = find_and_convert_to_type(float, wms_tree, ".//DataWindow/LowerRightX")
    bottom = find_and_convert_to_type(float, wms_tree, ".//DataWindow/LowerRightY")
    src_bounds = (left, bottom, right, up)
    bounds = bounds or src_bounds
    upper_bound_zoom = find_and_convert_to_type(int, wms_tree, ".//DataWindow/TileLevel")
    src_resolution = constants.MERCATOR_RESOLUTION_MAPPING[upper_bound_zoom]
    resolution = resolution or constants.MERCATOR_RESOLUTION_MAPPING[upper_bound_zoom]
    dst_width, dst_height, transform = rasterization.raster_data(bounds=bounds, dest_resolution=resolution)
    orig_width, orig_height, orig_transform = rasterization.raster_data(
        bounds=src_bounds, dest_resolution=src_resolution)
    src_window = from_bounds(*bounds, transform=orig_transform)
    projection = find_and_convert_to_type(str, wms_tree, ".//Projection")
    blockx = find_and_convert_to_type(str, wms_tree, ".//BlockSizeX")
    blocky = find_and_convert_to_type(str, wms_tree, ".//BlockSizeY")
    projection = CRS(init=projection)

    vrt = BaseVRT(dst_width, dst_height, projection, transform)

    vrt.add_metadata_attributes(domain="IMAGE_STRUCTURE")
    vrt.add_metadata_item(text="PIXEL", key="INTERLEAVE")

    bands_count = find_and_convert_to_type(int, wms_tree, ".//BandsCount")
    if bands_count != 3:
        raise ValueError("We support currently on 3 bands WMS")

    for idx, band in enumerate(["RED", "GREEN", "BLUE"]):
        bidx = idx + 1

        band_element = vrt.add_band("Byte", bidx, band)
        dst_window = Window(0, 0, dst_width, dst_height)

        vrt.add_band_simplesource(band_element, bidx, "Byte", False, os.path.abspath(wms_file),
                                  orig_width, orig_height, blockx, blocky,
                                  src_window, dst_window)

    return vrt.tostring()

def limit_to_bands_vrt(src_dataset, bands):
    height = src_dataset.height
    width = src_dataset.width
    dtype = str(src_dataset.dtype)
    vrt = BaseVRT(width, height, src_dataset.crs, src_dataset.affine)
    with rasterio.open(src_dataset._filename) as src:
        block_shapes = src.block_shapes

        for target_idx, source_band in enumerate(bands):
            source_idx = src_dataset.band_names.index(source_band)
            block_shape = block_shapes[source_idx]
            band_element = vrt.add_band(dtype, target_idx, "gray")
            window = Window(0, 0, height, width)
            vrt.add_band_simplesource(band_element, source_idx,
                                      dtype, False, src_dataset._filename, width, height,
                                      block_shape[1], block_shape[0], window, window
                                      )

        if all(MaskFlags.per_dataset in flags for flags in src.mask_flag_enums):
            mask_band = vrt.add_mask_band('Byte')
            source_idx = src_dataset.band_names.index(source_band)
            block_shape = block_shapes[source_idx]
            window = Window(0, 0, height, width)
            vrt.add_band_simplesource(mask_band, source_idx,
                                      dtype, False, src_dataset._filename, width, height,
                                      block_shape[1], block_shape[0], window, window
                                      )


        vrt.add_metadata_attributes(domain="telluric")
        vrt.add_metadata_item(test=",".join(bands), key="telluric_band_names")
        return vrt.tostring()


def boundless_vrt_doc(
        src_dataset, nodata=None, background=None, hidenodata=False,
        width=None, height=None, transform=None):
    """Make a VRT XML document.
    Parameters
    ----------
    src_dataset : Dataset
        The dataset to wrap.
    background : Dataset, optional
        A dataset that provides the optional VRT background. NB: this dataset
        must have the same number of bands as the src_dataset.
    Returns
    -------
    bytes
        An ascii-encoded string (an ElementTree detail)
    """

    nodata = nodata or src_dataset.nodata
    width = width or src_dataset.width
    height = height or src_dataset.height
    transform = transform or src_dataset.transform

    vrt = BaseVRT(width, height, src_dataset.crs, transform)

    for bidx, ci, block_shape, dtype in zip(src_dataset.indexes, src_dataset.colorinterp,
                                            src_dataset.block_shapes, src_dataset.dtypes):
        band_element = vrt.add_band(dtype, bidx, ci.name, nodata=nodata, hidenodata=True)

        if background is not None:
            src_window = Window(0, 0, background.width, background.height)
            dst_window = Window(0, 0, width, height)
            vrt.add_band_simplesource(band_element, bidx, dtype, False, background.name,
                                      width, height, block_shape[1], block_shape[0],
                                      src_window, dst_window)

        src_window = Window(0, 0, src_dataset.width, src_dataset.height)
        xoff = (src_dataset.transform.xoff - transform.xoff) / transform.a
        yoff = (src_dataset.transform.yoff - transform.yoff) / transform.e
        xsize = src_dataset.width * src_dataset.transform.a / transform.a
        ysize = src_dataset.height * src_dataset.transform.e / transform.e
        dst_window = Window(xoff, yoff, xsize, ysize)
        vrt.add_band_simplesource(band_element, bidx, dtype, False, src_dataset.name,
                                  width, height, block_shape[1], block_shape[0],
                                  src_window, dst_window, nodata=src_dataset.nodata)

    if all(MaskFlags.per_dataset in flags for flags in src_dataset.mask_flag_enums):
        mask_band = vrt.add_mask_band('Byte')
        src_window = Window(0, 0, src_dataset.width, src_dataset.height)
        xoff = (src_dataset.transform.xoff - transform.xoff) / transform.a
        yoff = (src_dataset.transform.yoff - transform.yoff) / transform.e
        xsize = src_dataset.width
        ysize = src_dataset.height
        dst_window = Window(xoff, yoff, xsize, ysize)
        vrt.add_band_simplesource(mask_band, 'mask,1', 'Byte', False, src_dataset.name,
                                  width, height, block_shape[1], block_shape[0], src_window, dst_window)
    return vrt.tostring()
