# -*- coding: utf-8 -*-
"""
Module containing the implementation of Geofile operations using a sql statement.
"""

from concurrent import futures
import datetime
from geofileops.util import general_util
import logging
import logging.config
import multiprocessing
from pathlib import Path
import shutil
from typing import List, Optional

from geofileops import geofile
from . import io_util
from . import ogr_util
from . import sqlite_util
from geofileops.geofile import GeometryType, PrimitiveType

################################################################################
# Some init
################################################################################

logger = logging.getLogger(__name__)

################################################################################
# Operations on one layer
################################################################################

def buffer(
        input_path: Path,
        output_path: Path,
        distance: float,
        quadrantsegments: int = 5,
        input_layer: str = None,
        output_layer: str = None,
        columns: List[str] = None,
        explodecollections: bool = False,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # If buffer distance < 0, necessary to apply a make_valid to evade invalid geometries 
    if distance < 0:
        # A negative buffer is only relevant for polygon types, so only keep polygon results
        # Negative buffer creates invalid stuff, and the st_simplify(geom, 0) seems the only function fixing this!
        #geom_operation_sqlite = f"ST_CollectionExtract(ST_makevalid(ST_simplify(ST_buffer({{geometrycolumn}}, {distance}, {quadrantsegments}), 0)), 3) AS geom"
        
        sql_template = f'''
            SELECT ST_CollectionExtract(ST_buffer({{geometrycolumn}}, {distance}, {quadrantsegments}), 3) AS geom
                  {{columns_to_select_str}} 
              FROM "{{input_layer}}"
             WHERE 1=1 
               {{batch_filter}}'''
    else:
        sql_template = f'''
            SELECT ST_Buffer({{geometrycolumn}}, {distance}, {quadrantsegments}) AS geom
                  {{columns_to_select_str}} 
              FROM "{{input_layer}}"
             WHERE 1=1 
               {{batch_filter}}'''

    # Buffer operation always results in polygons...
    force_output_geometrytype = GeometryType.MULTIPOLYGON
            
    return _single_layer_vector_operation(
            input_path=input_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='buffer',
            input_layer=input_layer,
            output_layer=output_layer,
            columns=columns,
            explodecollections=explodecollections,
            force_output_geometrytype=force_output_geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def isvalid(
        input_path: Path,
        output_path: Path,
        only_invalid: bool = False,
        input_layer: str = None,        
        output_layer: str = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False) -> bool:

    # Prepare sql template for this operation
    only_invalid_filter = ""
    if only_invalid is True:
         only_invalid_filter = "AND ST_IsValid({geometrycolumn}) <> 1"
    sql_template = f'''
            SELECT ST_IsValidDetail({{geometrycolumn}}) AS geom
                  ,ST_IsValid({{geometrycolumn}}) AS isvalid
                  ,ST_IsValidReason({{geometrycolumn}}) AS isvalidreason
                  {{columns_to_select_str}} 
              FROM "{{input_layer}}"
             WHERE 1=1 
               {only_invalid_filter}
               {{batch_filter}}'''

    _single_layer_vector_operation(
            input_path=input_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='isvalid',
            input_layer=input_layer,
            output_layer=output_layer,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)
    
    # If no invalid geoms are found, there won't be an output file and we can return True
    if not output_path.exists():
        return True
    else:
        layerinfo = geofile.get_layerinfo(output_path)
        logger.info(f"Found {layerinfo.featurecount} invalid geometries in {output_path}")
        return False

def convexhull(
        input_path: Path,
        output_path: Path,
        input_layer: str = None,
        output_layer: str = None,
        columns: Optional[List[str]] = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # Prepare sql template for this operation 
    sql_template = f'''
            SELECT ST_ConvexHull({{geometrycolumn}}) AS geom
                  {{columns_to_select_str}} 
              FROM "{{input_layer}}"
             WHERE 1=1 
               {{batch_filter}}'''

    # Output geometry type same as input geometry type 
    input_layer_info = geofile.get_layerinfo(input_path, input_layer)
    return _single_layer_vector_operation(
            input_path=input_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='convexhull',
            input_layer=input_layer,
            output_layer=output_layer,
            columns=columns,
            force_output_geometrytype=input_layer_info.geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def makevalid(
        input_path: Path,
        output_path: Path,
        input_layer: str = None,        
        output_layer: str = None,
        columns: Optional[List[str]] = None,
        explodecollections: bool = False,
        force_output_geometrytype: GeometryType = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # Prepare sql template for this operation 
    sql_template = f'''
            SELECT ST_MakeValid({{geometrycolumn}}) AS geom
                  {{columns_to_select_str}} 
              FROM "{{input_layer}}"
             WHERE 1=1 
               {{batch_filter}}'''
    
    # Specify output_geomatrytype, because otherwise makevalid results in 
    # column type 'GEOMETRY'/'UNKNOWN(ANY)' 
    if force_output_geometrytype is None:
        force_output_geometrytype = geofile.get_layerinfo(input_path, input_layer).geometrytype
    
    return _single_layer_vector_operation(
            input_path=input_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='makevalid',
            input_layer=input_layer,
            output_layer=output_layer,
            columns=columns,
            explodecollections=explodecollections,
            force_output_geometrytype=force_output_geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def select(
        input_path: Path,
        output_path: Path,
        sql_stmt: str,
        sql_dialect: str = 'SQLITE',
        input_layer: str = None,        
        output_layer: str = None,
        columns: Optional[List[str]] = None,
        explodecollections: bool = False,
        force_output_geometrytype: GeometryType = None,
        nb_parallel: int = 1,
        verbose: bool = False,
        force: bool = False):

    # Check if output exists already here, to evade to much logging to be written 
    if output_path.exists():
        if force is False:
            logger.info(f"Stop select: output exists already {output_path}")
            return
    if verbose:
        logger.info(f"  -> select to execute:\n{sql_stmt}")
    else:
        logger.debug(f"  -> select to execute:\n{sql_stmt}")
    
    # If no output geometrytype is specified, use the geometrytype of the input layer
    if force_output_geometrytype is None:
        force_output_geometrytype = geofile.get_layerinfo(input_path, input_layer).geometrytype
        logger.info(f"No force_output_geometrytype specified, so defaults to input layer geometrytype: {force_output_geometrytype}")

    # Go!
    return _single_layer_vector_operation(
            input_path=input_path,
            output_path=output_path,
            sql_template=sql_stmt,
            operation_name='select',
            input_layer=input_layer,
            output_layer=output_layer,
            columns=columns,
            explodecollections=explodecollections,
            force_output_geometrytype=force_output_geometrytype,
            filter_null_geoms=False,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def simplify(
        input_path: Path,
        output_path: Path,
        tolerance: float,        
        input_layer: str = None,        
        output_layer: str = None,
        columns: Optional[List[str]] = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # Prepare sql template for this operation 
    sql_template = f'''
            SELECT ST_Simplify({{geometrycolumn}}, {tolerance}) AS geom
                  {{columns_to_select_str}} 
              FROM "{{input_layer}}"
             WHERE 1=1 
               {{batch_filter}}'''

    # Output geometry type same as input geometry type 
    input_layer_info = geofile.get_layerinfo(input_path, input_layer)
    return _single_layer_vector_operation(
            input_path=input_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='simplify',
            input_layer=input_layer,
            output_layer=output_layer,
            columns=columns,
            force_output_geometrytype=input_layer_info.geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def _single_layer_vector_operation(
        input_path: Path,
        output_path: Path,
        sql_template: str,
        operation_name: str,
        input_layer: str = None,        
        output_layer: str = None,
        columns: List[str] = None,
        explodecollections: bool = False,
        force_output_geometrytype: GeometryType = None,
        filter_null_geoms: bool = True,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    ##### Init #####
    start_time = datetime.datetime.now()

    # Check input parameters...
    if not input_path.exists():
        raise Exception(f"Error {operation_name}: input_path doesn't exist: {input_path}")

    # Check/get layer names
    if input_layer is None:
        input_layer = geofile.get_only_layer(input_path)
    elif input_path.suffix.lower() == '.shp' and input_path.stem != input_layer:
        # For shapefiles, the file stem and the layer name should be the same!
        raise Exception(f"For Shapefiles, layername needs to be the same as file stem: input_path: {input_path.name} != input_layer: {input_layer}")   
    if output_layer is None:
        output_layer = geofile.get_default_layer(output_path)
    elif output_path.suffix.lower() == '.shp' and output_path.stem != output_layer:
        # For shapefiles, the file stem and the layer name should be the same!
        raise Exception(f"For Shapefiles, layername needs to be the same as file stem: output_path: {output_path.name} != output_layer: {output_layer}")

    # Check if spatialite is properly installed to execute this query
    if input_path.suffix.lower() == '.gpkg':
        ogr_util.get_gdal_to_use(sql_template)

    # If output file exists already, either clean up or return...
    if output_path.exists():
        if force is False:
            logger.info(f"Stop {operation_name}: output exists already {output_path}")
            return
        else:
            geofile.remove(output_path)

    ##### Calculate #####
    tempdir = io_util.create_tempdir(operation_name.replace(' ', '_'))
    
    try:
        input_tmp_path = input_path

        ##### Calculate #####
        # Calculating can be done in parallel, but only one process can write to 
        # the same file at the time... 
        layerinfo = geofile.get_layerinfo(input_path, input_layer)  
        if nb_parallel == -1:
            # Default, put at lease 100 rows in a batch for parallelisation
            max_parallel = int(layerinfo.featurecount/100)
            nb_parallel = min(multiprocessing.cpu_count(), max_parallel)

            # Don't use all processors so the machine stays accessible 
            if nb_parallel > 4:
                nb_parallel -= 1
            elif nb_parallel < 1:
                nb_parallel = 1                

        # If we are processing in parallel... seperate work in batches
        # Remark: especially for 'select' operation, if nb_parallel is 1 
        #         nb_batches should be 1 (select might give wrong results)
        if nb_parallel > 1:
            nb_batches = nb_parallel
        else:
            nb_batches = 1

        nb_done = 0
        with futures.ProcessPoolExecutor(nb_parallel) as calculate_pool:

            # Prepare columns to select
            columns_to_select_str = ''
            if columns is not None:
                # Case-insinsitive check if columns contains columns not in input layer...
                columns_orig_upper = [column.upper() for column in layerinfo.columns]
                missing_columns = [col for col in columns if (col.upper() not in columns_orig_upper)]
                if len(missing_columns) > 0:
                    raise Exception(f"Error, parameter columns contains columns not in input layer: {missing_columns}. Existing columns: {layerinfo.columns}")

                # Now concat columns
                columns_quoted = [f'"{col}"' for col in columns] 
                columns_to_select_str = f", {', '.join(columns_quoted)}"
            elif len(layerinfo.columns) > 0:
                # No columns specified, so take all columns of the layer 
                columns_quoted = [f'"{col}"' for col in layerinfo.columns]
                columns_to_select_str = f", {', '.join(columns_quoted)}"
            
            # Calculate the number of features per thread
            # Determine the min_rowid and max_rowid to devide the batches as good as possible
            sql_stmt = f'SELECT MIN(rowid) as min_rowid, MAX(rowid) as max_rowid FROM "{input_layer}"'
            result = geofile.read_file_sql(path=input_path, sql_stmt=sql_stmt, layer=input_layer)
            if len(result) == 1:
                min_rowid = result['min_rowid'].values[0]
                max_rowid = result['max_rowid'].values[0]
                nb_rowids_per_batch = (max_rowid - min_rowid)/nb_batches
            else:
                raise Exception(f"Error determining min_rowid and max_rowid for {input_path}, layer {input_layer}")
            row_limit = int(nb_rowids_per_batch/nb_batches)
            row_offset = 0

            # Prepare output filename
            tmp_output_path = tempdir / output_path.name

            translate_jobs = {}    
            future_to_batch_id = {}
            for batch_id in range(nb_batches):

                translate_jobs[batch_id] = {}
                translate_jobs[batch_id]['layer'] = output_layer

                output_tmp_partial_path = tempdir / f"{output_path.stem}_{batch_id}{output_path.suffix}"
                translate_jobs[batch_id]['tmp_partial_output_path'] = output_tmp_partial_path

                # For the last batch_id, take all rowid's left...
                if batch_id < nb_batches-1:
                    batch_filter = f"AND (rowid >= {row_offset} AND rowid < {row_offset + row_limit})"
                else:
                    batch_filter = f"AND rowid >= {row_offset}"

                # Now we have everything to format sql statement
                sql_stmt = sql_template.format(
                        geometrycolumn=layerinfo.geometrycolumn,
                        columns_to_select_str=columns_to_select_str,
                        input_layer=input_layer,
                        batch_filter=batch_filter)

                # Make sure no NULL geoms are outputted...
                if filter_null_geoms is True:
                    sql_stmt = f'''
                            SELECT sub.*
                            FROM
                                ( {sql_stmt}
                                ) sub
                            WHERE sub.geom IS NOT NULL'''

                translate_jobs[batch_id]['sql_stmt'] = sql_stmt
                translate_description = f"Async {operation_name} {batch_id} of {nb_batches}"
                # Remark: this temp file doesn't need spatial index
                translate_info = ogr_util.VectorTranslateInfo(
                        input_path=input_tmp_path,
                        output_path=output_tmp_partial_path,
                        translate_description=translate_description,
                        output_layer=output_layer,
                        sql_stmt=sql_stmt,
                        sql_dialect='SQLITE',
                        create_spatial_index=False,
                        explodecollections=explodecollections,
                        force_output_geometrytype=force_output_geometrytype,
                        verbose=verbose)
                future = calculate_pool.submit(
                        ogr_util.vector_translate_by_info,
                        info=translate_info)
                future_to_batch_id[future] = batch_id
                row_offset += row_limit
            
            # Loop till all parallel processes are ready, but process each one that is ready already
            for future in futures.as_completed(future_to_batch_id):
                try:
                    _ = future.result()
                except Exception as ex:
                    batch_id = future_to_batch_id[future]
                    raise Exception(f"Error executing {translate_jobs[batch_id]}") from ex

                # Start copy of the result to a common file
                # Remark: give higher priority, because this is the slowest factor
                batch_id = future_to_batch_id[future]
                tmp_partial_output_path = translate_jobs[batch_id]['tmp_partial_output_path']
                
                if tmp_partial_output_path.exists():
                    geofile.append_to(
                            src=tmp_partial_output_path, 
                            dst=tmp_output_path, 
                            dst_layer=output_layer,
                            create_spatial_index=False)
                    geofile.remove(tmp_partial_output_path)
                else:
                    if verbose:
                        logger.info(f"Result file {tmp_partial_output_path} was empty")

                # Log the progress and prediction speed
                nb_done += 1
                general_util.report_progress(
                        start_time, nb_done, nb_batches, operation_name, nb_parallel=nb_parallel)

        ##### Round up and clean up ##### 
        # Now create spatial index and move to output location
        if tmp_output_path.exists():
            geofile.create_spatial_index(path=tmp_output_path, layer=output_layer)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            geofile.move(tmp_output_path, output_path)
        else:
            logger.warning(f"Result of {operation_name} was empty!f")

    finally:
        # Clean tmp dir
        shutil.rmtree(tempdir)
        logger.info(f"Processing ready, took {datetime.datetime.now()-start_time}!")

################################################################################
# Operations on two layers
################################################################################

def erase(
        input_path: Path,
        erase_path: Path,
        output_path: Path,
        input_layer: str = None,
        input_columns: List[str] = None,
        input_columns_prefix: str = '',
        erase_layer: str = None,
        output_layer: str = None,
        explodecollections: bool = False,
        output_with_spatial_index: bool = True,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # Init
    # In the query, important to only extract the geometry types that are expected 
    input_layer_info = geofile.get_layerinfo(input_path, input_layer)
    primitivetypeid = input_layer_info.geometrytype.to_primitivetype.value

    # If the input type is not point, force the output type to multi, 
    # because erase can cause eg. polygons to be split to multipolygons...
    force_output_geometrytype = input_layer_info.geometrytype
    if force_output_geometrytype is not GeometryType.POINT:
        force_output_geometrytype = input_layer_info.geometrytype.to_multitype

    # Prepare sql template for this operation 
    # Remarks:
    #   - ST_difference(geometry , NULL) gives NULL as result! -> hence the CASE 
    #   - use of the with instead of an inline view is a lot faster
    #   - WHERE geom IS NOT NULL to evade rows with a NULL geom, they give issues in later operations
    sql_template = f'''
          SELECT * FROM (
            WITH layer2_unioned AS (
              SELECT layer1.rowid AS layer1_rowid
                    ,ST_union(layer2.{{input2_geometrycolumn}}) AS geom
                FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
               WHERE 1=1
                 {{batch_filter}}
                 AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                 AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                 AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                 AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
               GROUP BY layer1.rowid
            )
            SELECT CASE WHEN layer2_unioned.geom IS NULL THEN layer1.{{input1_geometrycolumn}}
                        ELSE ST_CollectionExtract(ST_difference(layer1.{{input1_geometrycolumn}}, layer2_unioned.geom), {primitivetypeid})
                   END as geom
                  {{layer1_columns_prefix_alias_str}}
              FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
              LEFT JOIN layer2_unioned ON layer1.rowid = layer2_unioned.layer1_rowid
             WHERE 1=1
               {{batch_filter}}
          )
          WHERE geom IS NOT NULL
            AND ST_NPoints(geom) > 0   -- ST_CollectionExtract outputs empty, but not NULL geoms in spatialite 4.3 
            '''
    
    # Go!
    return _two_layer_vector_operation(
            input1_path=input_path,
            input2_path=erase_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='erase',
            input1_layer=input_layer,
            input1_columns=input_columns,
            input1_columns_prefix=input_columns_prefix,
            input2_layer=erase_layer,
            input2_columns=None,
            output_layer=output_layer,
            explodecollections=explodecollections,
            force_output_geometrytype=force_output_geometrytype,
            output_with_spatial_index=output_with_spatial_index,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def export_by_location(
        input_to_select_from_path: Path,
        input_to_compare_with_path: Path,
        output_path: Path,
        min_area_intersect: Optional[float] = None,
        area_inters_column_name: Optional[str] = 'area_inters',
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input2_layer: str = None,
        input2_columns: List[str] = None,
        output_layer: str = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):
    
    # Prepare sql template for this operation 
    # TODO: test performance difference between the following two queries
    sql_template = f'''
            SELECT layer1.{{input1_geometrycolumn}} AS geom 
                  {{layer1_columns_prefix_alias_str}}
              FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
              JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
             WHERE 1=1
               {{batch_filter}}
               AND EXISTS (
                  SELECT 1 
                    FROM {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                    JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                   WHERE layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                     AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                     AND ST_intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                     AND ST_touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0)
            '''
    # Calculate intersect area if necessary
    area_inters_column_expression = ''
    if area_inters_column_name is not None or min_area_intersect is not None:
        if area_inters_column_name is None:
            area_inters_column_name = 'area_inters'
        area_inters_column_expression = f",ST_area(ST_intersection(ST_union(layer1.{{input1_geometrycolumn}}), ST_union(layer2.{{input2_geometrycolumn}}))) as {area_inters_column_name}"
    
    # Prepare sql template for this operation 
    sql_template = f'''
            SELECT ST_union(layer1.{{input1_geometrycolumn}}) as geom
                  {{layer1_columns_prefix_str}}
                  {area_inters_column_expression}
              FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
              JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
              JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
              JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
             WHERE 1=1
               {{batch_filter}}
               AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
               AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
               AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
               AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
             GROUP BY layer1.rowid {{layer1_columns_prefix_str}}
            '''

    # Filter on intersect area if necessary
    if min_area_intersect is not None:
        sql_template = f'''
                SELECT sub.* 
                  FROM 
                    ( {sql_template}
                    ) sub
                 WHERE sub.{area_inters_column_name} >= {min_area_intersect}'''

    # Go!
    input_layer_info = geofile.get_layerinfo(input_to_select_from_path, input1_layer)
    return _two_layer_vector_operation(
            input1_path=input_to_select_from_path,
            input2_path=input_to_compare_with_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='export_by_location',
            input1_layer=input1_layer,
            input1_columns=input1_columns,
            input2_layer=input2_layer,
            input2_columns=input2_columns,
            output_layer=output_layer,
            force_output_geometrytype=input_layer_info.geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def export_by_distance(
        input_to_select_from_path: Path,
        input_to_compare_with_path: Path,
        output_path: Path,
        max_distance: float,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input2_layer: str = None,
        output_layer: str = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # Prepare sql template for this operation 
    sql_template = f'''
            SELECT geom
                  {{layer1_columns_prefix_alias_str}}
                FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                WHERE 1=1
                  {{batch_filter}}
                  AND EXISTS (
                      SELECT 1 
                        FROM {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                        JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                        WHERE (layer1tree.minx-{max_distance}) <= layer2tree.maxx 
                          AND (layer1tree.maxx+{max_distance}) >= layer2tree.minx
                          AND (layer1tree.miny-{max_distance}) <= layer2tree.maxy 
                          AND (layer1tree.maxy+{max_distance}) >= layer2tree.miny
                          AND ST_distance(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) <= {max_distance})'''

    input_layer_info = geofile.get_layerinfo(input_to_select_from_path, input1_layer)

    # Go!
    return _two_layer_vector_operation(
            input1_path=input_to_select_from_path,
            input2_path=input_to_compare_with_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='export_by_distance',
            input1_layer=input1_layer,
            input1_columns=input1_columns,
            input2_layer=input2_layer,
            output_layer=output_layer,
            force_output_geometrytype=input_layer_info.geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def intersect(
        input1_path: Path,
        input2_path: Path,
        output_path: Path,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input1_columns_prefix: str = 'l1_',
        input2_layer: str = None,
        input2_columns: List[str] = None,
        input2_columns_prefix: str = 'l2_',
        output_layer: str = None,
        explodecollections: bool = False,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # In the query, important to only extract the geometry types that are expected 
    # TODO: test for geometrycollection, line, point,...
    input1_layer_info = geofile.get_layerinfo(input1_path, input1_layer)
    input2_layer_info = geofile.get_layerinfo(input2_path, input2_layer)
    primitivetype_to_extract = PrimitiveType(min(
            input1_layer_info.geometrytype.to_primitivetype.value, 
            input2_layer_info.geometrytype.to_primitivetype.value))

    # For the output file, if output is going to be polygon or linestring, force 
    # MULTI variant to evade ugly warnings
    force_output_geometrytype = primitivetype_to_extract.to_multitype

    # Prepare sql template for this operation 
    sql_template = f'''
        SELECT sub.geom
             {{layer1_columns_from_subselect_str}}
             {{layer2_columns_from_subselect_str}} 
          FROM
            ( SELECT ST_CollectionExtract(
                       ST_Intersection(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}), 
                       {primitivetype_to_extract.value}) as geom
                    {{layer1_columns_prefix_alias_str}}
                    {{layer2_columns_prefix_alias_str}}
                FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
               WHERE 1=1
                 {{batch_filter}}
                 AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                 AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                 AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                 AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
            ) sub
         WHERE sub.geom IS NOT NULL
        '''

    # Go!
    return _two_layer_vector_operation(
            input1_path=input1_path,
            input2_path=input2_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='intersect',
            input1_layer=input1_layer,
            input1_columns=input1_columns,
            input1_columns_prefix=input1_columns_prefix,
            input2_layer=input2_layer,
            input2_columns=input2_columns,
            input2_columns_prefix=input2_columns_prefix,
            output_layer=output_layer,
            explodecollections=explodecollections,
            force_output_geometrytype=force_output_geometrytype,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def join_by_location(
        input1_path: Path,
        input2_path: Path,
        output_path: Path,
        discard_nonmatching: bool = True,
        min_area_intersect: Optional[float] = None,
        area_inters_column_name: Optional[str] = None,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input1_columns_prefix: str = 'l1_',
        input2_layer: str = None,
        input2_columns: List[str] = None,
        input2_columns_prefix: str = 'l2_',
        output_layer: str = None,
        explodecollections: bool = False,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):
    
    # Prepare sql template for this operation 
    # Calculate intersect area if necessary
    area_inters_column_expression = ''
    if area_inters_column_name is not None or min_area_intersect is not None:
        if area_inters_column_name is None:
            area_inters_column_name = 'area_inters'
        area_inters_column_expression = f",ST_area(ST_intersection(ST_union(layer1.{{input1_geometrycolumn}}), ST_union(layer2.{{input2_geometrycolumn}}))) as {area_inters_column_name}"
    
    # Prepare sql template for this operation 
    if discard_nonmatching is True:
        # Use inner join
        sql_template = f'''
                SELECT layer1.{{input1_geometrycolumn}} as geom
                      {{layer1_columns_prefix_alias_str}}
                      {{layer2_columns_prefix_alias_str}}
                      {area_inters_column_expression}
                      ,ST_intersection(layer1.{{input1_geometrycolumn}}, 
                                       layer2.{{input2_geometrycolumn}}) as geom_intersect
                 FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                 JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                 JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                 JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                WHERE 1=1
                  {{batch_filter}}
                  AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                  AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                  AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                  AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
                '''
    else:
        # Left outer join 
        sql_template = f'''
                SELECT layer1.{{input1_geometrycolumn}} as geom /*ST_union(layer1.{{input1_geometrycolumn}}) as geom*/
                      {{layer1_columns_prefix_alias_str}}
                      {{layer2_columns_prefix_alias_str}}
                      {area_inters_column_expression}
                      ,ST_intersection(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) as geom_intersect
                 FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                 JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                 JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                 JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                WHERE 1=1
                  {{batch_filter}}
                  AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                  AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                  AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                  AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
                UNION ALL
                SELECT layer1.{{input1_geometrycolumn}} as geom
                      {{layer1_columns_prefix_alias_str}}
                      {{layer2_columns_prefix_alias_null_str}}
                      {area_inters_column_expression}
                      ,NULL as geom_intersect
                 FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                 JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                 WHERE 1=1
                  {{batch_filter}}
                  AND NOT EXISTS (
                      SELECT 1 
                        FROM {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                        JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                       WHERE layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                         AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                         AND ST_intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                         AND ST_touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0)
                '''
        
    # Filter on intersect area if necessary
    if min_area_intersect is not None:
        sql_template = f'''
                SELECT sub.* 
                  FROM 
                    ( {sql_template}
                    ) sub
                 WHERE sub.{area_inters_column_name} >= {min_area_intersect}'''

    input1_layer_info = geofile.get_layerinfo(input1_path, input1_layer)
    
    # Go!
    return _two_layer_vector_operation(
            input1_path=input1_path,
            input2_path=input2_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='join_by_location',
            input1_layer=input1_layer,
            input1_columns=input1_columns,
            input1_columns_prefix=input1_columns_prefix,
            input2_layer=input2_layer,
            input2_columns=input2_columns,
            input2_columns_prefix=input2_columns_prefix,
            output_layer=output_layer,
            force_output_geometrytype=input1_layer_info.geometrytype,
            explodecollections=explodecollections,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def select_two_layers(
        input1_path: Path,
        input2_path: Path,
        output_path: Path,
        sql_stmt: str,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input1_columns_prefix: str = 'l1_',
        input2_layer: str = None,
        input2_columns: List[str] = None,
        input2_columns_prefix: str = 'l2_',
        output_layer: str = None,
        force_output_geometrytype: GeometryType = None,
        explodecollections: bool = False,
        nb_parallel: int = 1,
        verbose: bool = False,
        force: bool = False):

    # Go!
    return _two_layer_vector_operation(
            input1_path=input1_path,
            input2_path=input2_path,
            output_path=output_path,
            sql_template=sql_stmt,
            operation_name='select_two_layers',
            input1_layer=input1_layer,
            input1_columns=input1_columns,
            input1_columns_prefix=input1_columns_prefix,
            input2_layer=input2_layer,
            input2_columns=input2_columns,
            input2_columns_prefix=input2_columns_prefix,
            output_layer=output_layer,
            force_output_geometrytype=force_output_geometrytype,
            explodecollections=explodecollections,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def split(
        input1_path: Path,
        input2_path: Path,
        output_path: Path,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input1_columns_prefix: str = 'l1_',
        input2_layer: str = None,
        input2_columns: List[str] = None,
        input2_columns_prefix: str = 'l2_',
        output_layer: str = None,
        explodecollections: bool = False,
        output_with_spatial_index: bool = True,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # In the query, important to only extract the geometry types that are 
    # expected, so the primitive type of input1_layer  
    # TODO: test for geometrycollection, line, point,...
    input1_layer_info = geofile.get_layerinfo(input1_path, input1_layer)
    primitivetype_to_extract = input1_layer_info.geometrytype.to_primitivetype
    
    # For the output file, force MULTI variant to evade ugly warnings
    force_output_geometrytype = primitivetype_to_extract.to_multitype

    # Prepare sql template for this operation 
    sql_template = f'''
            SELECT * FROM 
              ( WITH layer2_unioned AS (
                  SELECT layer1.rowid AS layer1_rowid
                        ,ST_union(layer2.{{input2_geometrycolumn}}) AS geom
                    FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                    JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                    JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                    JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                   WHERE 1=1
                     {{batch_filter}}
                     AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                     AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                     AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                     AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
                   GROUP BY layer1.rowid
                )
                SELECT ST_CollectionExtract(
                            ST_intersection(ST_union(layer1.{{input1_geometrycolumn}}), 
                                            ST_union(layer2.{{input2_geometrycolumn}})), 
                            {primitivetype_to_extract.value}) as geom
                      {{layer1_columns_prefix_alias_str}}
                      {{layer2_columns_prefix_alias_str}}
                 FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                 JOIN {{input1_databasename}}."rtree_{{input1_tmp_layer}}_{{input1_geometrycolumn}}" layer1tree ON layer1.fid = layer1tree.id
                 JOIN {{input2_databasename}}."{{input2_tmp_layer}}" layer2
                 JOIN {{input2_databasename}}."rtree_{{input2_tmp_layer}}_{{input2_geometrycolumn}}" layer2tree ON layer2.fid = layer2tree.id
                WHERE 1=1
                  {{batch_filter}}
                  AND layer1tree.minx <= layer2tree.maxx AND layer1tree.maxx >= layer2tree.minx
                  AND layer1tree.miny <= layer2tree.maxy AND layer1tree.maxy >= layer2tree.miny
                  AND ST_Intersects(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 1
                  AND ST_Touches(layer1.{{input1_geometrycolumn}}, layer2.{{input2_geometrycolumn}}) = 0
                GROUP BY layer1.rowid {{layer1_columns_prefix_str}}
                UNION ALL
                SELECT CASE WHEN layer2_unioned.geom IS NULL THEN layer1.{{input1_geometrycolumn}}
                            ELSE ST_CollectionExtract(
                                    ST_difference(layer1.{{input1_geometrycolumn}}, layer2_unioned.geom), 
                                    {primitivetype_to_extract.value})
                       END as geom
                       {{layer1_columns_prefix_alias_str}}
                       {{layer2_columns_prefix_alias_null_str}}
                  FROM {{input1_databasename}}."{{input1_tmp_layer}}" layer1
                  LEFT JOIN layer2_unioned ON layer1.rowid = layer2_unioned.layer1_rowid
                 WHERE 1=1
                   {{batch_filter}}
               ) 
             WHERE geom IS NOT NULL
               AND ST_NPoints(geom) > 0   -- ST_CollectionExtract outputs empty, but not NULL geoms in spatialite 4.3 
            '''
    
    # Go!
    return _two_layer_vector_operation(
            input1_path=input1_path,
            input2_path=input2_path,
            output_path=output_path,
            sql_template=sql_template,
            operation_name='split',
            input1_layer=input1_layer,
            input1_columns=input1_columns,
            input1_columns_prefix=input1_columns_prefix,
            input2_layer=input2_layer,
            input2_columns=input2_columns,
            input2_columns_prefix=input2_columns_prefix,
            output_layer=output_layer,
            force_output_geometrytype=force_output_geometrytype,
            explodecollections=explodecollections,
            output_with_spatial_index=output_with_spatial_index,
            nb_parallel=nb_parallel,
            verbose=verbose,
            force=force)

def union(
        input1_path: Path,
        input2_path: Path,
        output_path: Path,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input1_columns_prefix: str = 'l1_',
        input2_layer: str = None,
        input2_columns: List[str] = None,
        input2_columns_prefix: str = 'l2_',
        output_layer: str = None,
        explodecollections: bool = False,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    # A union can be simulated by doing a "split" of input1 and input2 and 
    # then append the result of an erase of input2 with input1... 

    # Because the calculations in split and erase will be towards temp files, 
    # we need to do some additional init + checks here...
    if force is False and output_path.exists():
        return
    if output_layer is None:
        output_layer = geofile.get_default_layer(output_path)

    tempdir = io_util.create_tempdir('union')
    try:
        # First split input1 with input2 to a temporary output file...
        split_output_path = tempdir / "split_output.gpkg"
        split(  input1_path=input1_path,
                input2_path=input2_path,
                output_path=split_output_path,
                input1_layer=input1_layer,
                input1_columns=input1_columns,
                input1_columns_prefix=input1_columns_prefix,
                input2_layer=input2_layer,
                input2_columns=input2_columns,
                input2_columns_prefix=input2_columns_prefix,
                output_layer=output_layer,
                explodecollections=explodecollections,
                output_with_spatial_index=False,
                nb_parallel=nb_parallel,
                verbose=verbose,
                force=force)

        # Now erase input1 from input2 to another temporary output file...
        erase_output_path = tempdir / "erase_output.gpkg"
        erase(  input_path=input2_path,
                erase_path=input1_path,
                output_path=erase_output_path,
                input_layer=input2_layer,
                input_columns=input2_columns,
                input_columns_prefix=input2_columns_prefix,
                erase_layer=input1_layer,
                output_layer=output_layer,
                explodecollections=explodecollections,
                output_with_spatial_index=False,
                nb_parallel=nb_parallel,
                verbose=verbose,
                force=force)
        
        # Now append 
        geofile._append_to_nolock(
            src=erase_output_path,
            dst=split_output_path,
            src_layer=output_layer,
            dst_layer=output_layer)

        # Create spatial index
        geofile.create_spatial_index(path=split_output_path, layer=output_layer)
        
        # Now we are ready to move the result to the final spot...
        if output_path.exists():
            geofile.remove(output_path)
        geofile.move(split_output_path, output_path)

    finally:
        shutil.rmtree(tempdir)

def _two_layer_vector_operation(
        input1_path: Path,
        input2_path: Path,
        output_path: Path,
        sql_template: str,
        operation_name: str,
        input1_layer: str = None,
        input1_columns: List[str] = None,
        input1_columns_prefix: str = 'l1_',
        input2_layer: str = None,
        input2_columns: List[str] = None,
        input2_columns_prefix: str = 'l2_',
        output_layer: str = None,
        explodecollections: bool = False,
        force_output_geometrytype: GeometryType = None,
        output_with_spatial_index: bool = True,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):
    """
    Executes an operation that needs 2 input files.
    
    Args:
        input1_path (str): the file to export features from
        input2_path (str): the file to check intersections with
        output_path (str): output file
        input1_layer (str, optional): [description]. Defaults to None.
        input1_columns
        input1_columns_prefix
        input2_layer (str, optional): [description]. Defaults to None.
        input2_columns
        input2_columns_prefix
        output_layer (str, optional): [description]. Defaults to None.
        explodecollections (bool, optional): Explode collecions in output. Defaults to False.
        force_output_geometrytype (GeometryType, optional): Defaults to None.
        nb_parallel (int, optional): [description]. Defaults to -1.
        force (bool, optional): [description]. Defaults to False.
    
    Raises:
        Exception: [description]
    """
    ##### Init #####
    if not input1_path.exists():
        raise Exception(f"Error {operation_name}: input1_path doesn't exist: {input1_path}")
    if not input2_path.exists():
        raise Exception(f"Error {operation_name}: input2_path doesn't exist: {input2_path}")
    if output_path.exists():
        if force is False:
            logger.info(f"Stop {operation_name}: output exists already {output_path}")
            return
        else:
            geofile.remove(output_path)

    # Check if spatialite is properly installed to execute this query
    sqlite_util.check_runtimedependencies()

    # Init layer info
    start_time = datetime.datetime.now()
    if input1_layer is None:
        input1_layer = geofile.get_only_layer(input1_path)
    if input2_layer is None:
        input2_layer = geofile.get_only_layer(input2_path)
    if output_layer is None:
        output_layer = geofile.get_default_layer(output_path)
    tempdir = io_util.create_tempdir(operation_name)

    try:
        ##### Prepare tmp files/batches #####
        logger.info(f"Prepare input (params) for {operation_name} with tempdir: {tempdir}")
        processing_params = _prepare_processing_params(
                input1_path=input1_path,
                input1_layer=input1_layer,
                input2_path=input2_path,
                input2_layer=input2_layer,
                tempdir=tempdir,
                nb_parallel=nb_parallel,
                verbose=verbose)

        ##### Calculate! #####
        # We need the input1 column names to format the select
        input1_tmp_layerinfo = geofile.get_layerinfo(processing_params.input1_path, processing_params.input1_layer)
        if input1_columns is not None:
            # Case-insensitive check if input1_columns contains columns not in layer...
            columns_orig_upper = [column.upper() for column in input1_tmp_layerinfo.columns]
            missing_columns = [col for col in input1_columns if (col.upper() not in columns_orig_upper)]                
            if len(missing_columns) > 0:
                raise Exception(f"Error, input1_columns contains columns not in input1_layer: {missing_columns}. Existing columns: {input1_tmp_layerinfo.columns}")
            layer1_columns = input1_columns
        else:
            layer1_columns = input1_tmp_layerinfo.columns
        layer1_columns_prefix_alias_str = ''
        layer1_columns_from_subselect_str = ''
        layer1_columns_prefix_str = ''
        if len(layer1_columns) > 0:
            layer1_columns_prefix_alias = [f'layer1."{column}" "{input1_columns_prefix}{column}"' for column in layer1_columns]
            layer1_columns_prefix_alias_str = ',' + ", ".join(layer1_columns_prefix_alias)
            layer1_columns_from_subselect = [f'sub."{input1_columns_prefix}{column}"' for column in layer1_columns]
            layer1_columns_from_subselect_str = ',' + ", ".join(layer1_columns_from_subselect)
            layer1_columns_prefix = [f'layer1."{column}"' for column in layer1_columns]
            layer1_columns_prefix_str = ',' + ", ".join(layer1_columns_prefix)

        # We need the input2 column names to format the select
        input2_tmp_layerinfo = geofile.get_layerinfo(processing_params.input2_path, processing_params.input2_layer)
        if input2_columns is not None:
            # Case-insinsitive check if input1_columns contains columns not in layer...
            columns_orig_upper = [column.upper() for column in input2_tmp_layerinfo.columns]
            missing_columns = [col for col in input2_columns if (col.upper() not in columns_orig_upper)]                
            if len(missing_columns) > 0:
                raise Exception(f"Error, input2_columns contains columns not in input2_layer: {missing_columns}. Existing columns: {input2_tmp_layerinfo.columns}")
            layer2_columns = input2_columns
        else:
            layer2_columns = input2_tmp_layerinfo.columns
        layer2_columns_prefix_alias_str = ''
        layer2_columns_prefix_alias_null_str = ''
        layer2_columns_from_subselect_str = ''
        layer2_columns_prefix_str = ''
        
        if len(layer2_columns) > 0:
            layer2_columns_prefix_alias = [f'layer2."{column}" "{input2_columns_prefix}{column}"' for column in layer2_columns]
            layer2_columns_prefix_alias_str = ',' + ", ".join(layer2_columns_prefix_alias)
            layer2_columns_prefix_alias_null = [f'NULL "{input2_columns_prefix}{column}"' for column in layer2_columns]
            layer2_columns_prefix_alias_null_str = ',' + ", ".join(layer2_columns_prefix_alias_null)
            layer2_columns_from_subselect = [f'sub."{input2_columns_prefix}{column}"' for column in layer2_columns]
            layer2_columns_from_subselect_str = ',' + ", ".join(layer2_columns_from_subselect)
            layer2_columns_prefix = [f'layer2."{column}"' for column in layer2_columns]
            layer2_columns_prefix_str = ',' + ", ".join(layer2_columns_prefix)

        # Prepare output filename
        tmp_output_path = tempdir / output_path.name
        tmp_output_path = output_path
        tmp_output_path.parent.mkdir(exist_ok=True, parents=True)
        
        ##### Calculate #####
        logger.info(f"Start {operation_name} in {processing_params.nb_parallel} parallel processes")

        # Calculating can be done in parallel, but only one process can write to 
        # the same file at the time... 
        with futures.ProcessPoolExecutor(processing_params.nb_parallel) as calculate_pool:

            # Start looping
            batches = {}    
            future_to_batch_id = {}
            for batch_id in processing_params.batches:

                batches[batch_id] = {}
                batches[batch_id]['layer'] = output_layer

                tmp_output_partial_path = tempdir / f"{output_path.stem}_{batch_id}.gpkg"
                batches[batch_id]['tmp_partial_output_path'] = tmp_output_partial_path

                sql_stmt = sql_template.format(
                        output_databasename='{output_databasename}',
                        input1_databasename='{input1_databasename}',
                        input2_databasename='{input2_databasename}',
                        layer1_columns_from_subselect_str=layer1_columns_from_subselect_str,
                        layer1_columns_prefix_alias_str=layer1_columns_prefix_alias_str,
                        input1_tmp_layer=processing_params.batches[batch_id]['layer'],
                        input1_geometrycolumn=input1_tmp_layerinfo.geometrycolumn,
                        layer2_columns_from_subselect_str=layer2_columns_from_subselect_str,
                        layer2_columns_prefix_alias_str=layer2_columns_prefix_alias_str,
                        layer2_columns_prefix_alias_null_str=layer2_columns_prefix_alias_null_str,
                        input2_tmp_layer=processing_params.input2_layer,
                        input2_geometrycolumn=input2_tmp_layerinfo.geometrycolumn,
                        layer1_columns_prefix_str=layer1_columns_prefix_str,
                        layer2_columns_prefix_str=layer2_columns_prefix_str,
                        batch_filter=processing_params.batches[batch_id]['batch_filter'])

                batches[batch_id]['sqlite_stmt'] = sql_stmt
                
                # Remark: this temp file doesn't need spatial index and use an 
                # aggressive speedy sqlite profile 
                future = calculate_pool.submit(
                        sqlite_util.create_table_as_sql,
                        input1_path=processing_params.batches[batch_id]['path'],
                        input1_layer=processing_params.batches[batch_id]['layer'],
                        input2_path=processing_params.input2_path, 
                        output_path=tmp_output_partial_path,
                        sql_stmt=sql_stmt,
                        output_layer=output_layer,
                        output_geometrytype=force_output_geometrytype,
                        create_spatial_index=False,
                        profile=sqlite_util.SqliteProfile.SPEED)
                future_to_batch_id[future] = batch_id
            
            # Loop till all parallel processes are ready, but process each one 
            # that is ready already
            nb_done = 0
            general_util.report_progress(
                    start_time, nb_done, len(processing_params.batches), 
                    operation_name, processing_params.nb_parallel)
            for future in futures.as_completed(future_to_batch_id):
                try:
                    # Get the result
                    result = future.result()
                    if result is not None and verbose is True:
                        logger.info(result)

                    # Start copy of the result to a common file
                    batch_id = future_to_batch_id[future]

                    # If the calculate gave results, copy to output
                    tmp_partial_output_path = batches[batch_id]['tmp_partial_output_path']
                    if tmp_partial_output_path.exists() and tmp_partial_output_path.stat().st_size > 0:
                        geofile.append_to(
                                src=tmp_partial_output_path, 
                                dst=tmp_output_path, 
                                create_spatial_index=False)
                        geofile.remove(tmp_partial_output_path)
                    else:
                        if verbose:
                            logger.info(f"Result file {tmp_partial_output_path} was empty")
                    
                except Exception as ex:
                    batch_id = future_to_batch_id[future]
                    #calculate_pool.shutdown()
                    logger.exception(f"Error executing {batches[batch_id]}")

                # Log the progress and prediction speed
                nb_done += 1
                general_util.report_progress(
                        start_time, nb_done, len(processing_params.batches), 
                        operation_name, processing_params.nb_parallel)

        ##### Round up and clean up ##### 
        # Now create spatial index and move to output location
        if tmp_output_path.exists():
            if output_with_spatial_index is True:
                geofile.create_spatial_index(path=tmp_output_path, layer=output_layer)
            if tmp_output_path != output_path:
                start_time_move = datetime.datetime.now()
                geofile.move(tmp_output_path, output_path)
                logger.info(f"move took {datetime.datetime.now()-start_time_move}!")
        else:
            logger.warning(f"Result of {operation_name} was empty!f")

        logger.info(f"{operation_name} ready, took {datetime.datetime.now()-start_time}!")
    except Exception as ex:
        logger.exception(f"{operation_name} ready with ERROR, took {datetime.datetime.now()-start_time}!")
    finally:
        shutil.rmtree(tempdir)

class ProcessingParams:
    def __init__(self,
            input1_path: Path = None,
            input1_layer: str = None,
            input2_path: Path = None,
            input2_layer: str = None,
            nb_parallel: int = -1,
            batches: dict = None):
        self.input1_path = input1_path
        self.input1_layer = input1_layer
        self.input2_path = input2_path
        self.input2_layer = input2_layer
        self.nb_parallel = nb_parallel
        self.batches = batches

def _prepare_processing_params(
        input1_path: Path,
        input1_layer: str,
        input2_path: Path,
        input2_layer: str,
        tempdir: Path,
        nb_parallel: int,
        verbose: bool = False) -> ProcessingParams:

    ### Init ###
    returnvalue = ProcessingParams(nb_parallel=nb_parallel)
    
    ### Determine the optimal number of parallel processes + batches ###
    if returnvalue.nb_parallel == -1:
        # Default, put at least 100 rows in a batch for parallelisation
        input1_layerinfo = geofile.get_layerinfo(input1_path, input1_layer)
        max_parallel = int(input1_layerinfo.featurecount/100)
        returnvalue.nb_parallel = min(multiprocessing.cpu_count(), max_parallel)

        # Don't use all processors so the machine stays accessible 
        if returnvalue.nb_parallel > 4:
            returnvalue.nb_parallel -= 1
        elif returnvalue.nb_parallel < 1:
            returnvalue.nb_parallel = 1

    # Determine optimal number of batches
    # Remark: especially for 'select' operation, if nb_parallel is 1 
    #         nb_batches should be 1 (select might give wrong results)
    if returnvalue.nb_parallel > 1:
        nb_batches = returnvalue.nb_parallel * 4
    else:
        nb_batches = 1

    ### Prepare input files for the calculation ###
    returnvalue.input1_layer = input1_layer
    returnvalue.input2_layer = input2_layer
    
    # If the input files is a geopackages, ok
    if input1_path.suffix.lower() == '.gpkg':
        returnvalue.input1_path = input1_path
    else:
        # If not geopackage copy the input layer to gpkg
        returnvalue.input1_path = tempdir / f"{input1_path.stem}.gpkg"
        ogr_util.vector_translate(
                input_path=input1_path,
                output_path=returnvalue.input1_path,
                input_layers=input1_layer,
                output_layer=returnvalue.input1_layer,
                verbose=verbose)        

    if input2_path.suffix.lower() == '.gpkg':
        returnvalue.input2_path = input2_path
    else:
        # If not geopackage copy the input layer to gpkg
        returnvalue.input2_path = tempdir / f"{input2_path.stem}.gpkg"
        ogr_util.vector_translate(
                input_path=input2_path,
                output_path=returnvalue.input2_path,
                input_layers=input2_layer,
                output_layer=returnvalue.input2_layer,
                verbose=verbose)

    ### Prepare batches to process ###
    # Get column names and info
    layer1_info = geofile.get_layerinfo(returnvalue.input1_path, returnvalue.input1_layer)
    
    # Check number of batches + appoint nb rows to batches
    nb_rows_input_layer = layer1_info.featurecount
    if nb_batches > int(nb_rows_input_layer/10):
        nb_batches = max(int(nb_rows_input_layer/10), 1)
    
    # Determine the min_rowid and max_rowid to divide the batches as good as possible
    sql_stmt = f'SELECT MIN(rowid) as min_rowid, MAX(rowid) as max_rowid FROM "{input1_layer}"'
    result = geofile.read_file_sql(path=returnvalue.input1_path, sql_stmt=sql_stmt, layer=input1_layer)
    if len(result) == 1:
        min_rowid = result['min_rowid'].values[0]
        max_rowid = result['max_rowid'].values[0]
        nb_rowids_per_batch = (max_rowid - min_rowid)/nb_batches
    else:
        raise Exception(f"Error determining min_rowid and max_rowid for {returnvalue.input1_path}, layer {input1_layer}")

    # Remark: adding data to a file in parallel using ogr2ogr gives locking 
    # issues on the sqlite file, so needs to be done sequential!
    batches = {}
    offset = 0
    for batch_id in range(nb_batches):
        
        # If they are all still in the same layer
        batches[batch_id] = {}
        batches[batch_id]['layer'] = returnvalue.input1_layer
        batches[batch_id]['path'] = returnvalue.input1_path
        
        # Use rowid filtering
        if nb_batches == 1:
            batches[batch_id]['batch_filter'] = ''
        elif batch_id < nb_batches:
            batches[batch_id]['batch_filter'] = f"AND (layer1.rowid >= {offset} AND layer1.rowid < {offset+nb_rowids_per_batch})"
            offset += nb_rowids_per_batch
        else:
            batches[batch_id]['batch_filter'] = f"AND layer1.rowid >= {offset}"

    # No use starting more processes than the number of batches...            
    if len(batches) < returnvalue.nb_parallel:
        returnvalue.nb_parallel = len(batches)
    
    returnvalue.batches = batches
    return returnvalue

def dissolve(
        input_path: Path,
        output_path: Path,
        groupby_columns: List[str] = None,
        explodecollections: bool = False,
        input_layer: str = None,        
        output_layer: str = None,
        verbose: bool = False,
        force: bool = False):

    ##### Init #####
    start_time = datetime.datetime.now()
    if output_path.exists():
        if force is False:
            logger.info(f"Stop dissolve: Output exists already {output_path}")
            return
        else:
            geofile.remove(output_path)

    if input_layer is None:
        input_layer = geofile.get_only_layer(input_path)
    if output_layer is None:
        output_layer = geofile.get_default_layer(output_path)

    # Prepare the strings to use in the select statement
    if groupby_columns is not None:
        # Because the query uses a subselect, the groupby columns need to be prefixed
        columns_with_prefix = [f't."{column}"' for column in groupby_columns]
        groupby_columns_str = ", ".join(columns_with_prefix)
        groupby_columns_for_groupby_str = groupby_columns_str
        groupby_columns_for_select_str = ", " + groupby_columns_str
    else:
        # Even if no groupby is provided, we still need to use a groupby clause, otherwise 
        # ST_union doesn't seem to work
        groupby_columns_for_groupby_str = "'1'"
        groupby_columns_for_select_str = ""

    # Remark: calculating the area in the enclosing selects halves the processing time

    sql_stmt = f"""
            SELECT sub.*, ST_area(sub.geom) AS area 
              FROM (SELECT ST_union(t.geom) AS geom{groupby_columns_for_select_str}
                      FROM {input_layer} t
                     GROUP BY {groupby_columns_for_groupby_str}) sub"""
    sql_stmt = f"""
            SELECT ST_union(t.geom) AS geom{groupby_columns_for_select_str}
              FROM {input_layer} t
             GROUP BY {groupby_columns_for_groupby_str}) sub"""
    sql_stmt = f"""
            SELECT ST_UnaryUnion(ST_Collect(t.geom)) AS geom{groupby_columns_for_select_str}
              FROM \"{input_layer}\" t
             GROUP BY {groupby_columns_for_groupby_str}"""
    sql_stmt = f"""
            SELECT ST_Collect(t.geom) AS geom{groupby_columns_for_select_str}
              FROM \"{input_layer}\" t"""
    sql_stmt = f"""
            SELECT ST_union(t.geom) AS geom{groupby_columns_for_select_str}
              FROM \"{input_layer}\" t"""

    sql_stmt = f"""
        SELECT ST_union(t.geom) AS geom{groupby_columns_for_select_str}
            FROM \"{input_layer}\" t
            GROUP BY {groupby_columns_for_groupby_str}"""

    translate_description = f"Dissolve {input_path}"
    ogr_util.vector_translate(
            input_path=input_path,
            output_path=output_path,
            translate_description=translate_description,
            output_layer=output_layer,
            sql_stmt=sql_stmt,
            sql_dialect='SQLITE',
            force_output_geometrytype=GeometryType.MULTIPOLYGON,
            explodecollections=explodecollections,
            verbose=verbose)

    logger.info(f"Processing ready, took {datetime.datetime.now()-start_time}!")

def dissolve_cardsheets(    
        input_path: Path,
        input_cardsheets_path: Path,
        output_path: Path,
        groupby_columns: List[str] = None,
        explodecollections: bool = False,
        input_layer: str = None,        
        output_layer: str = None,
        nb_parallel: int = -1,
        verbose: bool = False,
        force: bool = False):

    ##### Init #####
    start_time = datetime.datetime.now()
    if output_path.exists():
        if force is False:
            logger.info(f"Stop dissolve_cardsheets: output exists already {output_path}, so stop")
            return
        else:
            geofile.remove(output_path)
    if nb_parallel == -1:
        nb_parallel = multiprocessing.cpu_count()
        if nb_parallel > 4:
            nb_parallel -= 1

    # Get input data to temp gpkg file
    tempdir = io_util.create_tempdir("dissolve_cardsheets")
    input_tmp_path = tempdir / "input_layers.gpkg"
    if(input_path.suffix.lower() == '.gpkg'):
        logger.info(f"Copy {input_path} to {input_tmp_path}")
        geofile.copy(input_path, input_tmp_path)
        logger.debug("Copy ready")
    else:
        # Remark: this temp file doesn't need spatial index
        logger.info(f"Copy {input_path} to {input_tmp_path} using ogr2ogr")
        ogr_util.vector_translate(
                input_path=input_path,
                output_path=input_tmp_path,
                create_spatial_index=False,
                output_layer=input_layer,
                verbose=verbose)
        logger.debug("Copy ready")

    if input_layer is None:
        input_layer = geofile.get_only_layer(input_tmp_path)
    if output_layer is None:
        output_layer = geofile.get_default_layer(output_path)

    ##### Prepare tmp files #####

    # Prepare the strings to use in the select statement
    if groupby_columns is not None:
        # Because the query uses a subselect, the groupby columns need to be prefixed
        columns_with_prefix = [f"t.{column}" for column in groupby_columns]
        groupby_columns_str = ", ".join(columns_with_prefix)
        groupby_columns_for_groupby_str = groupby_columns_str
        groupby_columns_for_select_str = ", " + groupby_columns_str
    else:
        # Even if no groupby is provided, we still need to use a groupby clause, otherwise 
        # ST_union doesn't seem to work
        groupby_columns_for_groupby_str = "'1'"
        groupby_columns_for_select_str = ""

    # Load the cardsheets we want the dissolve to be bound on
    cardsheets_gdf = geofile.read_file(input_cardsheets_path)

    try:
        # Start calculation of intersections in parallel
        logger.info(f"Start calculation of dissolves in file {input_tmp_path} to partial files")
        tmp_output_path = tempdir / output_path.name

        with futures.ProcessPoolExecutor(nb_parallel) as calculate_pool:

            translate_jobs = {}    
            future_to_batch_id = {}    
            nb_batches = len(cardsheets_gdf)
            for batch_id, cardsheet in enumerate(cardsheets_gdf.itertuples()):
        
                translate_jobs[batch_id] = {}
                translate_jobs[batch_id]['layer'] = output_layer

                output_tmp_partial_path = tempdir / f"{output_path.stem}_{batch_id}{output_path.suffix}"
                translate_jobs[batch_id]['tmp_partial_output_path'] = output_tmp_partial_path

                # Remarks: 
                #   - calculating the area in the enclosing selects halves the processing time
                #   - ST_union() gives same performance as ST_unaryunion(ST_collect())!
                bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax = cardsheet.geometry.bounds  
                bbox_wkt = f"POLYGON (({bbox_xmin} {bbox_ymin}, {bbox_xmax} {bbox_ymin}, {bbox_xmax} {bbox_ymax}, {bbox_xmin} {bbox_ymax}, {bbox_xmin} {bbox_ymin}))"
                sql_stmt = f"""
                        SELECT ST_union(ST_intersection(t.geom, ST_GeomFromText('{bbox_wkt}'))) AS geom{groupby_columns_for_select_str}
                          FROM {input_layer} t
                          JOIN rtree_{input_layer}_geom t_tree ON t.fid = t_tree.id
                         WHERE t_tree.minx <= {bbox_xmax} AND t_tree.maxx >= {bbox_xmin}
                           AND t_tree.miny <= {bbox_ymax} AND t_tree.maxy >= {bbox_ymin}
                           AND ST_Intersects(t.geom, ST_GeomFromText('{bbox_wkt}')) = 1
                           AND ST_Touches(t.geom, ST_GeomFromText('{bbox_wkt}')) = 0
                         GROUP BY {groupby_columns_for_groupby_str}"""
                
                # Force geometrytype to multipolygon, because normal polygons easily are turned into 
                # multipolygon if self-touching...
                force_output_geometrytype = GeometryType.MULTIPOLYGON

                translate_jobs[batch_id]['sqlite_stmt'] = sql_stmt
                translate_description = f"Async dissolve {batch_id} of {nb_batches}, bounds: {cardsheet.geometry.bounds}"
                # Remark: this temp file doesn't need spatial index
                translate_info = ogr_util.VectorTranslateInfo(
                        input_path=input_tmp_path,
                        output_path=output_tmp_partial_path,
                        translate_description=translate_description,
                        output_layer=output_layer,
                        #clip_bounds=cardsheet.geometry.bounds,
                        sql_stmt=sql_stmt,
                        sql_dialect='SQLITE',
                        append=True,
                        update=True,
                        explodecollections=True,
                        force_output_geometrytype=force_output_geometrytype,
                        verbose=verbose)
                future = ogr_util.vector_translate_async(
                        concurrent_pool=calculate_pool, info=translate_info)
                future_to_batch_id[future] = batch_id
            
            # Loop till all parallel processes are ready, but process each one that is ready already
            for future in futures.as_completed(future_to_batch_id):
                try:
                    _ = future.result()

                    # Start copy of the result to a common file
                    # Remark: give higher priority, because this is the slowest factor
                    batch_id = future_to_batch_id[future]
                    # If the calculate gave results, copy to output
                    tmp_partial_output_path = translate_jobs[batch_id]['tmp_partial_output_path']
                    if tmp_partial_output_path.exists():
                        translate_description = f"Copy result {batch_id} of {nb_batches} to {output_layer}"
                        translate_info = ogr_util.VectorTranslateInfo(
                                input_path=tmp_partial_output_path,
                                output_path=tmp_output_path,
                                translate_description=translate_description,
                                output_layer=output_layer,
                                transaction_size=200000,
                                append=True,
                                update=True,
                                create_spatial_index=False,
                                force_output_geometrytype=GeometryType.MULTIPOLYGON,
                                priority_class='NORMAL',
                                verbose=verbose)
                        ogr_util.vector_translate_by_info(info=translate_info)
                        geofile.remove(tmp_partial_output_path)
                except Exception as ex:
                    batch_id = future_to_batch_id[future]
                    #calculate_pool.shutdown()
                    logger.error(f"Error executing {translate_jobs[batch_id]}: {ex}")

        ##### Round up and clean up ##### 
        # Now create spatial index and move to output location
        geofile.create_spatial_index(path=tmp_output_path, layer=output_layer)
        geofile.move(tmp_output_path, output_path)
    finally:
        # Clean tmp dir
        shutil.rmtree(tempdir)
        logger.info(f"Processing ready, took {datetime.datetime.now()-start_time}!")

if __name__ == '__main__':
    raise Exception("Not implemented!")