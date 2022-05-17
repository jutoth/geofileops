# geofileops [![Actions Status](https://github.com/geofileops/geofileops/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/geofileops/geofileops/actions?query=workflow%3ATests) [![Coverage Status](https://codecov.io/gh/geofileops/geofileops/branch/master/graph/badge.svg)](https://codecov.io/gh/geofileops/geofileops)
Library to make spatial operations on large geo files fast(er) and easy.

Remarks: 
* Most typical operations are available: buffer, simplify, dissolve, union, erase/difference, intersection,...
* The speed (improvement) depends on the operation, the number of available cores and the size of the input files.
  * For CPU bound operations (eg. union,... between large input files) the processing time will decrease depending on the number of available CPU cores. In extreme cases (very large files) the processing time can be divided by the number of available cores.
  * For dissolve on (very) large files, the speed improvement can be more than the processing time divided by the available cores.
  * For small files and/or computationally easy operations (eg. buffer) geofileops might be slower than other libraries.
* Tested on geopackage and shapefile input/output files. However, geopackage is highly recommended as it will offer better performance in geofileops... and also for the reasons listed here: www.switchfromshapefile.org.

Documentation on how to use geofileops can be found [here](https://geofileops.readthedocs.io).

The following chart gives an impression of the speed improvement that can be expected when processing larger files (including I/O!). More information about this benchmark can be found [here](https://github.com/geofileops/geobenchmark).

![Geo benchmark](https://github.com/geofileops/geobenchmark/blob/main/results/GeoBenchmark.png)
