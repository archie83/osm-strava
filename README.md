# osm-strava

## Detection of missing ways in OpenStreetMap based on Strava heatmaps

### Introduction

This program will detect missing ways in OSM for your favorite area, and generate a GeoJSON file that will enable you to create a [MapRoulette](https://maproulette.org/) challenge.

### Usage

```
Usage: strava.py [-h] [-a AREA] [-d DISTANCE] [-m MINLEVEL] [-o OFFSET] [-z ZOOM] [-s SIZE] [-b TASKS_DB] [-g GEOJSON] [-v] [-q] [-x X] [-y Y] [--debug]

optional arguments:
  -h, --help            show this help message and exit
  -a AREA, --area AREA  Area of interest (GeoJSON)
  -d DISTANCE, --distance DISTANCE
                        Maximum distance between Strava hot point and OSM way (default = 35 m)
  -m MINLEVEL, --minlevel MINLEVEL
                        Minimum Strava level (0-255) (default = 100)
  -o OFFSET, --offset OFFSET
                        Strava tile offset (0-3) 
  -z ZOOM, --zoom ZOOM  Strava zoom level (10-15) (default = 15)
  -s SIZE, --size SIZE  Minimum size of Strava trace (in pixels) (default = 20)
  -b TASKS_DB, --tasks_db TASKS_DB
                        Tasks database
  -g GEOJSON, --geojson GEOJSON
                        Output file
  -v, --verbose         Display more information
  -q, --quiet           Do not display progress
  -x X, --x X           Strava Tile x coordinate
  -y Y, --y Y           Strava Tile y coordinate
  --debug               Debug mode
```

### Description of parameters

#### -a \<GeoJSON file\>, --area \<GeoJSON file\>

The GeoJSON file must contains a multipolygon (or a collection of) describing the limits of the area of interest. You can download these files on the [OSM-Boundaries](https://osm-boundaries.com/) website.

#### -m <Level>, --minlevel <Level>

The threshold of Strava traces intensity. The range is [0,255] and the default value is 100.

#### -d <Distance>, --distance <Distance>

The minimal distance between an OSM way and the Strava

#### -s SIZE, --size SIZE

Minimum size of Strava trace (in pixels) (default = 20)
