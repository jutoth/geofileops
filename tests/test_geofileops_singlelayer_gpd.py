# -*- coding: utf-8 -*-
"""
Tests for operations using GeoPandas.
"""

import inspect
import json
from pathlib import Path
import sys

import geopandas as gpd
import shapely.geometry as sh_geom

# Add path so the local geofileops packages are found 
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import geofileops as gfo
from geofileops import GeometryType
from geofileops.util import _geoops_gpd
from geofileops.util import geometry_util
from geofileops.util import io_util
from tests import test_helper

def get_nb_parallel() -> int:
    # The number of parallel processes to use for these tests.
    return 2

def get_batchsize() -> int:
    return 5

def test_get_parallelization_params():
    parallelization_params = _geoops_gpd.get_parallelization_params(500000)
    assert parallelization_params is not None

def test_apply(tmpdir):
    # Init
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Prepare test data + test
    for suffix in test_helper.get_test_suffix_list():
        for crs_epsg in test_helper.get_test_crs_epsg_list():

            # Write test file in the right format + crs
            print(f"Run test for suffix {suffix} and crs_epsg {crs_epsg}")
            test_gdf = gpd.GeoDataFrame(
                    geometry=[
                            test_helper.TestData.polygon_small_island, 
                            test_helper.TestData.polygon_with_island, 
                            None], 
                    crs=test_helper.TestData.crs_epsg)
            # Reproject if needed
            if test_helper.TestData.crs_epsg != crs_epsg:
                test_gdf = test_gdf.to_crs(epsg=crs_epsg)
                assert isinstance(test_gdf, gpd.GeoDataFrame)
            input_path = tmp_dir / f"polygons_small_holes_{crs_epsg}{suffix}"
            gfo.to_file(test_gdf, input_path)
            output_path = tmp_dir / f"{input_path.stem}-output{suffix}"

            # Now run test
            basetest_apply(input_path, output_path)

def basetest_apply(
        input_path: Path,
        output_path: Path):
    ### Init ###
    input_layerinfo = gfo.get_layerinfo(input_path)
    
    ### Test apply with only_geom_input = True ###
    gfo.apply(
            input_path=input_path,
            output_path=output_path,
            func=lambda geom: geometry_util.remove_inner_rings(
                    geometry=geom,
                    min_area_to_keep=2,
                    crs=input_layerinfo.crs),
            only_geom_input=True,
            nb_parallel=get_nb_parallel())

    # Now check if the output file is correctly created
    assert output_path.exists() == True
    output_layerinfo = gfo.get_layerinfo(output_path)
    # The row with the None geometry will be removed
    assert input_layerinfo.featurecount == (output_layerinfo.featurecount + 1)
    assert len(output_layerinfo.columns) == len(input_layerinfo.columns)
    assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON 

    # Read result for some more detailed checks
    output_gdf = gfo.read_file(output_path)

    # In the 1st polygon the island should be removed 
    output_geometry = output_gdf['geometry'][0]
    assert output_geometry is not None
    if isinstance(output_geometry, sh_geom.MultiPolygon):
        assert len(output_geometry.geoms) == 1
        output_geometry = output_geometry.geoms[0]
    assert isinstance(output_geometry, sh_geom.Polygon)
    assert len(output_geometry.interiors) == 0

    # In the 2nd polygon the island is too large, so should still be there 
    output_geometry = output_gdf['geometry'][1]
    assert output_geometry is not None
    if isinstance(output_geometry, sh_geom.MultiPolygon):
        assert len(output_geometry.geoms) == 1
        output_geometry = output_geometry.geoms[0]
    assert isinstance(output_geometry, sh_geom.Polygon)
    assert len(output_geometry.interiors) == 1

    ### Test apply with only_geom_input = False ###
    output_path = io_util.with_stem(output_path, f"{output_path.stem}_2")
    gfo.apply(
            input_path=input_path,
            output_path=output_path,
            func=lambda row: geometry_util.remove_inner_rings(
                    row.geometry,
                    min_area_to_keep=2,
                    crs=input_layerinfo.crs),
            only_geom_input=False,
            nb_parallel=get_nb_parallel())

    # Now check if the output file is correctly created
    assert output_path.exists() == True
    output_layerinfo = gfo.get_layerinfo(output_path)
    assert input_layerinfo.featurecount == (output_layerinfo.featurecount + 1)
    assert len(output_layerinfo.columns) == len(input_layerinfo.columns)
    assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON 

    # Read result for some more detailed checks
    output_gdf = gfo.read_file(output_path)
    for index in range(0, 2):
        output_geometry = output_gdf['geometry'][index]
        assert output_geometry is not None
        if isinstance(output_geometry, sh_geom.MultiPolygon):
            assert len(output_geometry.geoms) == 1
            output_geometry = output_geometry.geoms[0]
        assert isinstance(output_geometry, sh_geom.Polygon)
        
        if index == 0:
            # In the 1st polygon the island must be removed 
            assert len(output_geometry.interiors) == 0
        elif index == 1:
            # In the 2nd polygon the island is larger, so should be there 
            assert len(output_geometry.interiors) == 1

def test_buffer_ext(tmpdir):    
    # Prepare test data + run tests
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for suffix in test_helper.get_test_suffix_list():
        # Buffer on not-projected data is weird, so not tested (at the moment)  
        for crs_epsg in test_helper.get_test_crs_epsg_list():
            # If test input file is in wrong format, convert it
            input_path = test_helper.prepare_test_file(
                    input_path=test_helper.TestFiles.polygons_parcels_gpkg,
                    output_dir=tmp_dir,
                    suffix=suffix,
                    crs_epsg=crs_epsg)

            # Now run test
            output_path = tmp_dir / f"{input_path.stem}-output{suffix}"
            print(f"Run test for suffix {suffix}, crs_epsg {crs_epsg}")
            basetest_buffer_ext(input_path, output_path)

def basetest_buffer_ext(input_path, output_path):

    ### Init ###    
    layerinfo_input = gfo.get_layerinfo(input_path)
    assert layerinfo_input.crs is not None
    distance = 1
    if layerinfo_input.crs.is_projected is False:
        # 1 degree = 111 km or 111000 m
        distance /= 111000

    ### Run standard buffer to compare with ###
    gfo.buffer(
            input_path=input_path,
            output_path=output_path,
            distance=distance,
            nb_parallel=get_nb_parallel())

    # Read result
    output_gdf = gfo.read_file(output_path)
    assert output_gdf['geometry'][0] is not None
    area_default_buffer = sum(output_gdf.area)
    
    ### Test polygon buffer with square endcaps ###
    output_path = output_path.parent / f"{output_path.stem}_endcap_join{output_path.suffix}"
    gfo.buffer(
            input_path=input_path,
            output_path=output_path,
            distance=distance,
            endcap_style=geometry_util.BufferEndCapStyle.SQUARE,
            join_style=geometry_util.BufferJoinStyle.MITRE,
            nb_parallel=get_nb_parallel())

    # Now check if the output file is correctly created
    assert output_path.exists() == True
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_input.featurecount == layerinfo_output.featurecount
    assert len(layerinfo_output.columns) == len(layerinfo_input.columns)
    assert layerinfo_output.geometrytype == GeometryType.MULTIPOLYGON 

    # Read result for some more detailed checks
    output_gdf = gfo.read_file(output_path)
    assert output_gdf['geometry'][0] is not None
    area_square_buffer = sum(output_gdf.area)
    assert area_square_buffer > area_default_buffer

    ### Check if ... parameter works ###
    # TODO: increase test coverage of other options...

def test_dissolve_linestrings(tmpdir):
    # Prepare test data + run tests
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for suffix in test_helper.get_test_suffix_list():
        for crs_epsg in test_helper.get_test_crs_epsg_list():
            # If test input file is in wrong format, convert it
            input_path = test_helper.prepare_test_file(
                    input_path=test_helper.TestFiles.linestrings_watercourses_gpkg,
                    output_dir=tmp_dir,
                    suffix=suffix,
                    crs_epsg=crs_epsg)

            # Now run test
            output_path = tmp_dir / f"{input_path.stem}-output{suffix}"
            print(f"Run test for suffix {suffix}, crs_epsg {crs_epsg}")
            basetest_dissolve_linestrings(input_path, output_path)

def basetest_dissolve_linestrings(input_path, output_basepath):
    # Dissolve, no groupby, explodecollections=True
    output_path = (output_basepath.parent / 
            f"{output_basepath.stem}_expl{output_basepath.suffix}")
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=True,
            nb_parallel=get_nb_parallel(),
            batchsize=5)

    # Check if the result file is correctly created
    assert output_path.exists() == True
    layerinfo_orig = gfo.get_layerinfo(input_path)
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_output.featurecount == 83
    assert layerinfo_output.geometrytype in [GeometryType.LINESTRING, GeometryType.MULTILINESTRING]
    assert len(layerinfo_output.columns) >= 0

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

    # Dissolve, no groupby, explodecollections=False
    output_path = (output_basepath.parent / 
            f"{output_basepath.stem}_noexpl{output_basepath.suffix}")
    # explodecollections=False only supported if 
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=False,
            nb_parallel=get_nb_parallel(),
            batchsize=5)

    # Check if the result file is correctly created
    assert output_path.exists() == True
    layerinfo_orig = gfo.get_layerinfo(input_path)
    
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_output.featurecount == 1
    assert layerinfo_output.geometrytype is layerinfo_orig.geometrytype
    assert len(layerinfo_output.columns) >= 0

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

    # Dissolve, groupby, explodecollections=False
    output_path = (output_basepath.parent / 
            f"{output_basepath.stem}_noexpl{output_basepath.suffix}")
    # explodecollections=False only supported if 
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=False,
            nb_parallel=get_nb_parallel(),
            batchsize=5)

    # Check if the result file is correctly created
    assert output_path.exists() == True
    layerinfo_orig = gfo.get_layerinfo(input_path)
    
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_output.featurecount == 1
    assert layerinfo_output.geometrytype is layerinfo_orig.geometrytype
    assert len(layerinfo_output.columns) >= 0

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

def test_dissolve_polygons_groupby(tmpdir):
    # Prepare test data + run tests
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for suffix in test_helper.get_test_suffix_list():
        for crs_epsg in test_helper.get_test_crs_epsg_list():
            # If test input file is in wrong format, convert it
            input_path = test_helper.prepare_test_file(
                    input_path=test_helper.TestFiles.polygons_parcels_gpkg,
                    output_dir=tmp_dir,
                    suffix=suffix,
                    crs_epsg=crs_epsg)

            # Now run test
            output_path = tmp_dir / f"{input_path.stem}-output{suffix}"
            print(f"Run test for suffix {suffix}, crs_epsg {crs_epsg}")
            basetest_dissolve_polygons_groupby(input_path, output_path)

def basetest_dissolve_polygons_groupby(
        input_path: Path, 
        output_basepath: Path):
    # Init
    layerinfo_input = gfo.get_layerinfo(input_path)

    ### Test dissolve polygons with groupby + without explodecollections ###
    output_path = output_basepath.parent / f"{output_basepath.stem}_group{output_basepath.suffix}"
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            groupby_columns=['GEWASGROEP'],
            explodecollections=False,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize())

    # Now check if the tmp file is correctly created
    assert output_path.exists() == True
    output_layerinfo = gfo.get_layerinfo(output_path)
    assert output_layerinfo.featurecount == 6
    assert len(output_layerinfo.columns) == 1
    assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == output_layerinfo.featurecount
    assert output_gdf['geometry'][0] is not None

    ### Test dissolve polygons with groupby + explodecollections ###
    output_path = output_basepath.parent / f"{output_basepath.stem}_group_explode{output_basepath.suffix}"
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            groupby_columns=['GEWASGROEP'],
            explodecollections=True,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize())

    # Now check if the tmp file is correctly created
    assert output_path.exists() == True
    output_layerinfo = gfo.get_layerinfo(output_path)
    assert output_layerinfo.featurecount == 25
    assert len(output_layerinfo.columns) == 1
    assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON 

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == output_layerinfo.featurecount
    assert output_gdf['geometry'][0] is not None

    ### Test dissolve polygons with groupby + agg_columns to columns ###
    output_path = output_basepath.parent / f"{output_basepath.stem}_group_aggcolumns{output_basepath.suffix}"
    # Remarks: 
    #     - column names are shortened so it also works for shapefile! 
    #     - the columns for agg_columns are choosen so they do not contain
    #       unique values, to be a better test case!
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            groupby_columns=["GEWASGROEP"],
            agg_columns={"columns":[
                    {"column": "lblhfdtlt", "agg": "max", "as": "lbl_max"},
                    {"column": "lblhfdtlt", "agg": "count", "as": "lbl_count"},
                    {"column": "lblhfdtlt", "agg": "count", "distinct": True, "as": "lbl_cnt_d"},
                    {"column": "lblhfdtlt", "agg": "concat", "as": "lbl_conc"},
                    {"column": "lblhfdtlt", "agg": "concat", "sep": ";", "as": "lbl_conc_s"},
                    {"column": "lblhfdtlt", "agg": "concat", "distinct": True, "as": "lbl_conc_d"},
                    {"column": "hfdtlt", "agg": "mean", "as": "tlt_mea"},
                    {"column": "hfdtlt", "agg": "min", "as": "tlt_min"},
                    {"column": "hfdtlt", "agg": "sum", "as": "tlt_sum"}]},
            explodecollections=False,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize())

    # Now check if the tmp file is correctly created
    assert output_path.exists() == True
    output_layerinfo = gfo.get_layerinfo(output_path)
    assert output_layerinfo.featurecount == 6
    assert len(output_layerinfo.columns) == 1 + 9
    assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON 

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == output_layerinfo.featurecount
    assert output_gdf["geometry"][0] is not None

    # Check agg_columns results
    grasland_idx = output_gdf[output_gdf["GEWASGROEP"] == "Grasland"].index.to_list()[0]
    assert output_gdf["lbl_max"][grasland_idx] == "Grasland"
    assert output_gdf["lbl_count"][grasland_idx] == 30
    print(f"output_gdf.lbl_concat_distinct: {output_gdf['lbl_conc_d'][grasland_idx]}")
    assert output_gdf["lbl_cnt_d"][grasland_idx] == 1
    assert output_gdf["lbl_conc"][grasland_idx].startswith("Grasland,Grasland,")
    assert output_gdf["lbl_conc_s"][grasland_idx].startswith("Grasland;Grasland;")
    assert output_gdf["lbl_conc_d"][grasland_idx] == "Grasland"
    assert output_gdf["tlt_mea"][grasland_idx] == 60  # type: ignore
    assert int(output_gdf["tlt_min"][grasland_idx]) == 60  # type: ignore
    assert output_gdf["tlt_sum"][grasland_idx] == 1800  # type: ignore

    groenten_idx = output_gdf[
            output_gdf["GEWASGROEP"] == "Groenten, kruiden en sierplanten"].index.to_list()[0]
    assert output_gdf["lbl_count"][groenten_idx] == 5
    print(f"groenten.lblhfdtlt_concat_distinct: {output_gdf['lbl_conc_d'][groenten_idx]}")
    assert output_gdf["lbl_cnt_d"][groenten_idx] == 4
        
    ### Test dissolve polygons with groupby + agg_columns to json ###
    output_path = output_basepath.parent / f"{output_basepath.stem}_group_aggjson{output_basepath.suffix}"
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            groupby_columns=["GEWASGROEP"],
            agg_columns={"json": ["lengte", "oppervl", "lblhfdtlt"]},
            explodecollections=False,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize())

    # Now check if the tmp file is correctly created
    assert output_path.exists() == True
    output_layerinfo = gfo.get_layerinfo(output_path)
    assert output_layerinfo.featurecount == 6
    assert len(output_layerinfo.columns) == 2
    assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON 

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == output_layerinfo.featurecount
    assert output_gdf["geometry"][0] is not None
    
    # In shapefiles, the length of str columns is very limited, so the json 
    # test would fail.
    if input_path.suffix != ".shp":
        grasland_json = json.loads(output_gdf["json"][0])
        assert len(grasland_json) == 30

    ### Test dissolve polygons with specified output layer ###
    # A different output layer is not supported for shapefile!!!
    try:
        output_path = output_basepath.parent / f"{output_basepath.stem}_group_outputlayer{output_basepath.suffix}"
        gfo.dissolve(
                input_path=input_path,
                output_path=output_path,
                groupby_columns=["GEWASGROEP"],
                output_layer="banana",
                explodecollections=True,
                nb_parallel=get_nb_parallel(),
                batchsize=get_batchsize())
    except Exception as ex:
        # A different output_layer is not supported for shapefile, so normal 
        # that an exception is thrown!
        assert output_path.suffix.lower() == '.shp'

    # Now check if the tmp file is correctly created
    if output_path.suffix.lower() != '.shp':
        assert output_path.exists() == True
        output_layerinfo = gfo.get_layerinfo(output_path)
        assert output_layerinfo.featurecount == 25
        assert len(output_layerinfo.columns) == 1
        assert output_layerinfo.name == 'banana'
        assert output_layerinfo.geometrytype == GeometryType.MULTIPOLYGON

        # Now check the contents of the result file
        input_gdf = gfo.read_file(input_path)
        output_gdf = gfo.read_file(output_path)
        assert input_gdf.crs == output_gdf.crs
        assert len(output_gdf) == output_layerinfo.featurecount
        assert output_gdf['geometry'][0] is not None

def test_dissolve_polygons_nogroupby(tmpdir):
    # Prepare test data + run tests
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for suffix in test_helper.get_test_suffix_list():
        for crs_epsg in test_helper.get_test_crs_epsg_list():
            # If test input file is in wrong format, convert it
            input_path = test_helper.prepare_test_file(
                    input_path=test_helper.TestFiles.polygons_parcels_gpkg,
                    output_dir=tmp_dir,
                    suffix=suffix,
                    crs_epsg=crs_epsg)

            # Now run test
            output_path = tmp_dir / f"{input_path.stem}-output{suffix}"
            print(f"Run test for suffix {suffix}, crs_epsg {crs_epsg}")
            basetest_dissolve_polygons_groupby(input_path, output_path)

def basetest_dissolve_polygons_nogroupby(
        input_path: Path, 
        output_basepath: Path):
    # Init
    layerinfo_input = gfo.get_layerinfo(input_path)
    
    ### Test dissolve polygons with explodecollections=True (= default) ###
    output_path = output_basepath.parent / f"{output_basepath.stem}_defaults{output_basepath.suffix}"
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=True,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize(),
            force=True)

    # Now check if the result file is correctly created
    assert output_path.exists() == True
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_output.featurecount == 23
    if output_basepath.suffix == '.shp':
        # Shapefile always has an FID field
        # TODO: think about whether this should also be the case for geopackage??? 
        assert len(layerinfo_output.columns) == 1
    else:
        assert len(layerinfo_output.columns) == 0

    # Check geometry type
    assert layerinfo_output.geometrytype == GeometryType.MULTIPOLYGON 

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

    ### Test dissolve to existing output path ###
    assert output_path.exists() is True
    mtime_orig = output_path.stat().st_mtime
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=True,
            nb_parallel=get_nb_parallel())
    assert output_path.stat().st_mtime == mtime_orig

    # With force=True
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=True,
            nb_parallel=get_nb_parallel(),
            force=True)
    assert output_path.stat().st_mtime == mtime_orig

    ### Test dissolve polygons with explodecollections=False ###
    output_path = output_basepath.parent / f"{output_basepath.stem}_defaults{output_basepath.suffix}"
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=False,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize(),
            force=True)

    # Now check if the result file is correctly created
    assert output_path.exists() == True
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_output.featurecount == 1
    if output_basepath.suffix == '.shp':
        # Shapefile always has an FID field
        # TODO: think about whether this should also be the case for geopackage??? 
        assert len(layerinfo_output.columns) == 1
    else:
        assert len(layerinfo_output.columns) == 0

    # Check geometry type
    assert layerinfo_output.geometrytype == GeometryType.MULTIPOLYGON 

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

    ### Test dissolve polygons, with output_layer ###
    # A different output layer is not supported for shapefile!!!
    try:
        output_path = output_basepath.parent / f"{output_basepath.stem}_outputlayer{output_basepath.suffix}"
        gfo.dissolve(
                input_path=input_path,
                output_path=output_path,
                output_layer='banana',
                explodecollections=True,
                nb_parallel=get_nb_parallel(),
                batchsize=get_batchsize(),
                force=True)
    except Exception as ex:
        # A different output_layer is not supported for shapefile, so normal 
        # that an exception is thrown!
        assert output_path.suffix.lower() == '.shp'

    # Now check if the result file is correctly created
    if output_path.suffix.lower() != '.shp':
        assert output_path.exists() == True
        layerinfo_output = gfo.get_layerinfo(output_path)
        assert layerinfo_output.featurecount == 23
        assert len(layerinfo_output.columns) == 0
        if output_basepath.suffix == '.shp':
            # Shapefile doesn't support specifying an output_layer
            assert layerinfo_output.name == output_path.stem
        else:
            assert layerinfo_output.name == 'banana'

        # Check geometry type
        assert layerinfo_output.geometrytype == GeometryType.MULTIPOLYGON 

        # Now check the contents of the result file
        input_gdf = gfo.read_file(input_path)
        output_gdf = gfo.read_file(output_path)
        assert input_gdf.crs == output_gdf.crs
        assert len(output_gdf) == layerinfo_output.featurecount
        assert output_gdf['geometry'][0] is not None

def test_dissolve_multisinglepolygons(tmpdir):
    # Init
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Prepare test data + test
    for suffix in test_helper.get_test_suffix_list():
        for crs_epsg in test_helper.get_test_crs_epsg_list():

            # Write test file in the right format + crs
            print(f"Run test for suffix {suffix} and crs_epsg {crs_epsg}")
            test_gdf = gpd.GeoDataFrame(
                    geometry=[
                            test_helper.TestData.polygon_with_island, 
                            test_helper.TestData.multipolygon], 
                    crs=test_helper.TestData.crs_epsg)
            
            # Reproject if needed
            if test_helper.TestData.crs_epsg != crs_epsg:
                test_gdf = test_gdf.to_crs(epsg=crs_epsg)
                assert isinstance(test_gdf, gpd.GeoDataFrame)
            input_path = tmp_dir / f"polygons_{crs_epsg}{suffix}"
            gfo.to_file(test_gdf, input_path)
            output_path = tmp_dir / f"{input_path.stem}-output{suffix}"

            # Now run test
            basetest_dissolve_multisinglepolygons(input_path, output_path)

def basetest_dissolve_multisinglepolygons(
        input_path: Path,
        output_path: Path):
    # Test to check if it is handled well that a file that results in single 
    # and multipolygons during dissolve is treated correctly, as geopackage 
    # doesn't support single and multi-polygons in one layer.
    gfo.dissolve(
            input_path=input_path,
            output_path=output_path,
            explodecollections=True,
            nb_squarish_tiles=2,
            nb_parallel=get_nb_parallel(),
            batchsize=get_batchsize(),
            force=True)

    # Now check if the result file is correctly created
    assert output_path.exists() == True
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_output.featurecount == 3
    if output_path.suffix == '.shp':
        # Shapefile always has an FID field
        # TODO: think about whether this should also be the case for geopackage??? 
        assert len(layerinfo_output.columns) == 1
    else:
        assert len(layerinfo_output.columns) == 0
    
    # Check geometry type
    assert layerinfo_output.geometrytype == GeometryType.MULTIPOLYGON 

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

def test_simplify_algorythms(tmpdir):
    """
    Default algorythms are tested in simplify_basic. 
    """
    # Init
    tmp_dir = Path(tmpdir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    test_inputs = []
    test_inputs.append({
            "input_path": test_helper.TestFiles.polygons_parcels_gpkg,
            "geometrytype": GeometryType.MULTIPOLYGON})
    test_inputs.append({
            "input_path": test_helper.TestFiles.points_gpkg,
            "geometrytype": GeometryType.MULTIPOINT})
    test_inputs.append({
            "input_path": test_helper.TestFiles.linestrings_rows_of_trees_gpkg,
            "geometrytype": GeometryType.MULTILINESTRING})

    # Prepare test data + run tests
    for suffix in test_helper.get_test_suffix_list():
        for crs_epsg in test_helper.get_test_crs_epsg_list():
            for test_input in test_inputs: 
                # If test input file is in wrong format, convert it
                input_path = test_helper.prepare_test_file(
                        input_path=test_input['input_path'],
                        output_dir=tmp_dir,
                        suffix=suffix,
                        crs_epsg=crs_epsg)

                # Now run test
                output_path = tmp_dir / f"{input_path.stem}-output{suffix}"
                print(f"Run test for suffix {suffix}, crs_epsg {crs_epsg}, geometrytype {test_input['geometrytype']}")
                basetest_simplify_algorythms(input_path, output_path, test_input['geometrytype'])

def basetest_simplify_algorythms(
        input_path: Path, 
        output_basepath: Path, 
        expected_output_geometrytype: GeometryType):
    """
    Default algorythms are tested in simplify_basic. 
    """
    ### Init ###
    layerinfo_orig = gfo.get_layerinfo(input_path)
    assert layerinfo_orig.crs is not None
    if layerinfo_orig.crs.is_projected:
        tolerance = 5
    else:
        # 1 degree = 111 km or 111000 m
        tolerance = 5/111000

    ### Test vw (visvalingam-whyatt) algorithm ###
    output_path = io_util.with_stem(output_basepath, f"{output_basepath.stem}_vw")
    gfo.simplify(
            input_path=input_path,
            output_path=output_path,
            tolerance=tolerance,
            algorithm=geometry_util.SimplifyAlgorithm.VISVALINGAM_WHYATT,
            nb_parallel=get_nb_parallel())

    # Now check if the tmp file is correctly created
    assert output_path.exists() == True
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_orig.featurecount == layerinfo_output.featurecount
    assert len(layerinfo_orig.columns) == len(layerinfo_output.columns)

    # Check geometry type
    assert layerinfo_output.geometrytype == expected_output_geometrytype

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

    ### Test lang algorithm ###
    output_path = io_util.with_stem(output_basepath, f"{output_basepath.stem}_lang")
    gfo.simplify(
            input_path=input_path,
            output_path=output_path,
            tolerance=tolerance,
            algorithm=geometry_util.SimplifyAlgorithm.LANG,
            lookahead=8,
            nb_parallel=get_nb_parallel())

    # Now check if the tmp file is correctly created
    assert output_path.exists() == True
    layerinfo_output = gfo.get_layerinfo(output_path)
    assert layerinfo_orig.featurecount == layerinfo_output.featurecount
    assert len(layerinfo_orig.columns) == len(layerinfo_output.columns)

    # Check geometry type
    assert layerinfo_output.geometrytype == expected_output_geometrytype

    # Now check the contents of the result file
    input_gdf = gfo.read_file(input_path)
    output_gdf = gfo.read_file(output_path)
    assert input_gdf.crs == output_gdf.crs
    assert len(output_gdf) == layerinfo_output.featurecount
    assert output_gdf['geometry'][0] is not None

if __name__ == '__main__':
    # Init
    tmpdir = test_helper.init_test_for_debug(Path(__file__).stem)

    # Run
    #test_get_parallelization_params()
    #test_apply(tmpdir / "apply")
    #test_buffer(tmpdir / "buffer")
    #test_buffer_ext(tmpdir / "buffer_ext")
    #test_dissolve_linestrings(tmpdir / "dissolve_linestrings_nogroupby")
    test_dissolve_polygons_groupby(tmpdir / "dissolve_polygons_groupby")
    #test_dissolve_polygons_nogroupby(tmpdir / "dissolve_polygons_nogroupby")
    #test_dissolve_multisinglepolygons(tmpdir / "dissolve_multisinglepolygons")
    #test_simplify(tmpdir / "simplify")