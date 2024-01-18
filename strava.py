#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ******************************************************************************
#
import os
import argparse
import json
import math
import sys
import time
import requests
from PIL import Image, ImageDraw
import numpy as np
import xml.etree.ElementTree as ET
from shapely.geometry.polygon import Polygon
from shapely.geometry import shape, GeometryCollection
import sqlite3


def print_debug(*args):
    if debug:
        print(*args)


def print_verbose(*args):
    if verbose:
        print(*args)


# Convert geographical coordinates to tile number
def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 1 << zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


# Convert tile number to geographical coordinates
def num2deg(xtile, ytile, zoom):
    n = 1 << zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


RADIUS = 6378137.0  # in meters on the equator


# Convert latitude to northing (Pseudo-Mercator projection)
def lat2y(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * RADIUS


# Convert longitude to easting (Pseudo-Mercator projection)
def lon2x(lon):
    return math.radians(lon) * RADIUS


# Convert northing (Pseudo-Mercator projection) to latitude
def y2lat(y):
    return math.degrees(2 * math.atan(math.exp(y / RADIUS)) - math.pi / 2.0)


# Convert easting (Pseudo-Mercator projection) to longitude
def x2lon(x):
    return math.degrees(x / RADIUS)


# Get bounding box of a Strava tile in geographical coordinates
def get_geo_bbox(x, y, zoom):
    (lat_ul, lon_ul) = num2deg(x, y, zoom)
    (lat_lr, lon_lr) = num2deg(x + 1, y + 1, zoom)
    return lat_ul, lon_ul, lat_lr, lon_lr


# Get bounding box of a Strava tile in pseudo-Mercator coordinates
def get_merc_bbox(x, y, zoom):
    (lat_ul, lon_ul, lat_lr, lon_lr) = get_geo_bbox(x, y, zoom)
    return lat2y(lat_ul), lon2x(lon_ul), lat2y(lat_lr), lon2x(lon_lr)


# Transforms projected coordinates to image coordinates
def transform(coords_merc, bbox_merc, pixel_size):
    transformed = []
    for coord in coords_merc:
        transformed.append((round((coord[0] - bbox_merc[1]) / pixel_size),
                            round((bbox_merc[0] - coord[1]) / pixel_size)))
    return transformed


# Transforms image coordinates to projected coordinates
def reverse_transform(coords, bbox_merc, pixel_size):
    return (x2lon(coords[1] * pixel_size + bbox_merc[1]),
            y2lat(bbox_merc[0] - coords[0] * pixel_size))


# Plot a black line on the image
def plot_line(draw, coords_merc, bbox, w, pixel_size):
    draw.line(transform(coords_merc, bbox, pixel_size), fill=0, width=w)


# Plot a polygon on the image
def plot_polygon(draw, coords_merc, bbox, pixel_size):
    draw.polygon(transform(coords_merc, bbox, pixel_size), outline=0, fill=0)


# Plot a circle on the image
def plot_circle(draw, center_merc, bbox, diameter, pixel_size):
    radius = diameter / 2
    centers = transform(center_merc, bbox, pixel_size)
    for center in centers:
        bbox = [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]
        draw.ellipse(bbox, fill=0)


# Recursive routine to measure the area of the trace on heatmap
def check_trace_area(image, row, col, target_color, min_size, size):
    # Check if the current pixel is within the image boundaries
    if row < 0 or row >= len(image) or col < 0 or col >= len(image[0]):
        return size

    # Check if the color of the current pixel matches the target color
    if image[row][col] < target_color:
        return size

    # Change the color of the current pixel to the replacement color
    image[row][col] = 0
    size = size + 1
    if size > min_size:   # To avoid stack overflow
        return size

    # Recursively call check_trace_area on adjacent pixels
    size = check_trace_area(image, row + 1, col, target_color, min_size, size)  # Down
    size = check_trace_area(image, row - 1, col, target_color, min_size, size)  # Up
    size = check_trace_area(image, row, col + 1, target_color, min_size, size)  # Right
    size = check_trace_area(image, row, col - 1, target_color, min_size, size)  # Left
    return size


# Check if Strava file is available in cache and download it if not in cache
def fetch_strava_tile(zoom, x, y):
    cache_dir = '/var/cache/strava'
    cache_file_path = os.path.join(cache_dir, activity, str(zoom), str(x), str(y) + '.png')
    if os.path.isfile(cache_file_path):
        if os.path.getsize(cache_file_path) > 0:
            print_verbose("Tile in cache:", cache_file_path)
            return cache_file_path
        else:
            print_verbose("Empty tile in cache :", cache_file_path)
            return None
    dir1 = os.path.join(cache_dir, activity, str(zoom))
    if not os.path.isdir(dir1):
        os.makedirs(dir1, exist_ok=True)
    dir2 = os.path.join(dir1, str(x))
    if not os.path.isdir(dir2):
        os.mkdir(dir2)

    url = f'https://strava-heatmap.tiles.freemap.sk/{activity}/hot/{zoom}/{x}/{y}.png'
    print_verbose("Downloading Strava tile at", url)
    try:
        r = requests.get(url, allow_redirects=True)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print_debug("Status code =", e.response.status_code)
        if e.response.status_code == 404:
            open(cache_file_path, 'wb').write(r.content)    # Write an empty file
        else:
            print(e, file=sys.stderr)
        cache_file_path = None
    except requests.exceptions.RequestException as e:
        print(e, file=sys.stderr)
        cache_file_path = None
    else:
        open(cache_file_path, 'wb').write(r.content)
    return cache_file_path


# Overpass request to download OSM ways in a bbox
def overpass_request(lat_ul_merc, lon_ul_merc, lat_lr_merc, lon_lr_merc):
    lat_lr = y2lat(lat_lr_merc)
    lon_ul = x2lon(lon_ul_merc)
    lat_ul = y2lat(lat_ul_merc)
    lon_lr = x2lon(lon_lr_merc)

    url = "https://overpass-api.de/api/interpreter?data=" + requests.utils.quote(
        f'[bbox:{lat_lr},{lon_ul},{lat_ul},{lon_lr}];'
        '(nwr[highway];'
        'nwr[railway];'
        'nwr[leisure~"track|pitch"];'
        'nwr[route=ferry];);out geom;')

    for retries in range(10):
        r = requests.get(url, allow_redirects=True, stream=True)
        osm_root = ET.fromstring(r.content)
        if osm_root.find('meta') is not None:   # Check that the result is not empty
            return osm_root
        time.sleep(5)
    else:
        print("No answer from Overpass server")
        sys.exit(1)


# This routine check if a strava heatmap tile contains a way not in OSM
# ---------------------------------------------------------------------
def check_strava_tile(polygon_area, x, y, zoom):
    # Get bounding box of strava tile in geographical coordinates
    (lat_ul, lon_ul, lat_lr, lon_lr) = get_geo_bbox(x, y, zoom)

    # Create a polygon for the bounding box
    polygon_strava = Polygon(((lon_ul, lat_ul), (lon_lr, lat_ul),
                             (lon_lr, lat_lr), (lon_ul, lat_lr), (lon_ul, lat_ul)))

    # Checks if the tile is in the area
    if polygon_area is None or polygon_strava.intersects(polygon_area):
        strava_tile = fetch_strava_tile(zoom, x, y)         # Get Strava tile
        if strava_tile is None:
            return
        try:
            image = Image.open(strava_tile)
        except Exception:
            print(f"Warning: Invalid Strava tile {strava_tile}", file=sys.stderr)
            print_debug("Strava image size = ", image.size)
            return
        draw = ImageDraw.Draw(image)

#        if debug:
#            # Fill with white pixels to display the mask
#            draw.rectangle((0,0,511,511), fill=255, outline=255)

        # Get bounding box of strava tile in Mercator coordinates
        (lat_ul_merc, lon_ul_merc, lat_lr_merc, lon_lr_merc) = get_merc_bbox(x, y, zoom)
        pixel_size = (lat_ul_merc - lat_lr_merc) / image.size[0]
        print_debug("Pixel size =", pixel_size)
        width = round(distance / pixel_size) * 2 + 1
        print_debug("Line width =", width)

        # Overpass request to get all OSM ways in the Strava tile bounding box
        osm_root = overpass_request(lat_ul_merc + distance, lon_ul_merc - distance,
                                    lat_lr_merc - distance, lon_lr_merc + distance)

        # Draw the OSM ways with black color on the Strava image
        for way in osm_root.iter('way'):
            area = False
            coords = []
            for tag in way.iter('tag'):
                if (tag.attrib["k"] == "area" and tag.attrib["v"] == "yes") or \
                   (tag.attrib["k"] == "leisure" and tag.attrib["v"] != "track"):
                    area = True
                if tag.attrib["k"] == "area" and tag.attrib["v"] == "no":
                    area = False
            for node in way.iter('nd'):
                coords.append((lon2x(float(node.attrib["lon"])), lat2y(float(node.attrib["lat"]))))
            if area:
                plot_polygon(draw, coords, get_merc_bbox(x, y, zoom), pixel_size)
            plot_line(draw, coords, get_merc_bbox(x, y, zoom), width, pixel_size)
            plot_circle(draw, coords, get_merc_bbox(x, y, zoom), width, pixel_size)

        # Draw the OSM multipolygons with black color on the Strava image
        for relation in osm_root.iter('relation'):
            area = False
            coords = []
            for tag in relation.iter('tag'):
                if (tag.attrib["k"] == "area" and tag.attrib["v"] == "yes") or \
                   (tag.attrib["k"] == "leisure" and tag.attrib["v"] != "track"):
                    area = True
                if tag.attrib["k"] == "type" and tag.attrib["v"] == "multipolygon":
                    area = True
                if tag.attrib["k"] == "area" and tag.attrib["v"] == "no":
                    area = False
            for node in relation.iter('nd'):
                coords.append((lon2x(float(node.attrib["lon"])), lat2y(float(node.attrib["lat"]))))
            if area:
                plot_polygon(draw, coords, get_merc_bbox(x, y, zoom), pixel_size)
            plot_line(draw, coords, get_merc_bbox(x, y, zoom), width, pixel_size)
            plot_circle(draw, coords, get_merc_bbox(x, y, zoom), width, pixel_size)

        if debug:
            image.save(f"test_{zoom}_{x}_{y}.png")  # For debugging

        data = np.array(image)
        # Loop while the lighter pixel in the Strava tile is above the threshold
        while np.max(data) >= threshold:
            maximum = np.max(data)
            max_index = np.unravel_index(np.argmax(data), data.shape)
            result = reverse_transform(max_index, get_merc_bbox(x, y, zoom), pixel_size)
            size = 0
            size = check_trace_area(data, max_index[0], max_index[1], threshold, min_size, size)
            if size > min_size:             # Is the size of the trace larger than the min size ?
                # print(f"geo:{result[1]},{result[0]}?z={zoom}")
                print_verbose(f"https://www.openstreetmap.org/?mlat={result[1]}&"
                              f"mlon={result[0]}#map={zoom}/{result[1]}/{result[0]}&layers=N")

                id = f"{zoom}/{x}/{y}/{max_index[0]}/{max_index[1]}"   # Unique ID for the MR task
                status = ""
                if tasks_db is not None:
                    # Check if this task has already been processed
                    res = cur.execute("SELECT TaskStatus,Mapper,TaskLink FROM tasks "
                                      f"WHERE TaskName='{id}'").fetchone()
                    if res is not None:
                        status = res[0]
                        print_verbose(status, ":", res[2][21:-2])

                if status == "Fixed" or status == "Already_Fixed":
                    print(f"Warning: This task has been marked as fixed by {res[1]},"
                          f" but it seems it is not: {res[2][21:-2]}/inspect", file=sys.stderr)

                if status != "Too_Hard" and status != "Not_an_Issue":
                    # print GEOJSON line for MapRoulette
                    RS = chr(30)  # Record Separator ASCII control character
                    print(f'{RS}{{"type":"FeatureCollection","features":[{{"type":"Feature",'
                          f'"geometry":{{"type":"Point","coordinates":[{result[0]}, {result[1]}]}},'
                          f'"properties":{{"id":"{id}","latitude":"{result[0]}",'
                          f'"longitude":"{result[1]}","distance":"{distance}",'
                          f'"threshold":"{threshold}","maximum":"{maximum}",'
                          f'"threshold":"{threshold}","min_size":"{min_size}","size":"{size}"}}}}],'
                          f'"id":"{id}"}}', file=geojson_file)

                # Flood fill to disable the area of the issue that has been found
                if debug:
                    image.save(f"before_flood_{zoom}_{x}_{y}.png")  # For debugging
                print_debug(x, y, max_index, maximum)
                ImageDraw.floodfill(image, (max_index[1], max_index[0]), 0, thresh=maximum - 1)
                data = np.array(image)
                if debug:
                    image.save(f"after_flood_{zoom}_{x}_{y}.png")  # For debugging


# Parse command line arguments
# ----------------------------
parser = argparse.ArgumentParser()

parser.add_argument("-a", "--area", help="Area of interest (GeoJSON)")
parser.add_argument("-m", "--minlevel", type=int, default=100,
                    help="Minimum Strava level (0-255)")
parser.add_argument("-d", "--distance", type=int, default=35,
                    help="Maximum distance between Strava hot point and OSM way")
parser.add_argument("-s", "--size", type=int, default=20,
                    help="Minimum size of Strava trace (in pixels)")
parser.add_argument("-z", "--zoom", default=15,
                    help="Strava zoom level (10-15)")
parser.add_argument("-c", "--activity", default='run',
                    help="Strava activity (default=run)")
parser.add_argument("-o", "--offset", type=int,
                    help="Strava tile offset (0-3)")
parser.add_argument("-b", "--tasks_db",
                    help="Tasks database")
parser.add_argument("-g", "--geojson",
                    help="Output file")
parser.add_argument('-v', '--verbose', action='store_true',
                    help="Display more information")
parser.add_argument('-q', '--quiet', action='store_true',
                    help="Do not display progress")
parser.add_argument('-x', '--x', type=int,
                    help="Strava Tile x coordinate")
parser.add_argument('-y', '--y', type=int,
                    help="Strava Tile y coordinate")
parser.add_argument('--debug', action='store_true',
                    help="Debug mode")

args = parser.parse_args()

verbose = args.verbose
debug = args.debug
distance = args.distance
print_verbose("Maximum distance = ", distance)
threshold = args.minlevel
print_verbose("Threshold = ", threshold)
zoom = args.zoom
min_size = args.size
print_verbose("Minimum size = ", min_size)
activity = args.activity
print_verbose("Activity = ", activity)
tasks_db = args.tasks_db

# Create output file
if args.geojson is not None:
    geojson_file = open(args.geojson, "w")
else:
    geojson_file = sys.stdout

if tasks_db is not None:
    # Create connection to "not_an_issue" database, and create a cursor
    con = sqlite3.connect(f"file:{tasks_db}?mode=ro", uri=True)
    cur = con.cursor()

if args.x is not None and args.y is not None:
    x = args.x
    y = args.y
    check_strava_tile(None, x, y, zoom)
    exit(0)

if args.x is not None or args.y is not None:
    print("Error: you must provide both x and y tile coordinates")
    exit(1)

if args.area is None:
    print("Error: you must provide either an area, either tile coordinates")
    exit(1)

# Get polygon of area
with open(args.area) as f:
    features = json.load(f)["features"]

# NOTE: buffer(0) is a trick for fixing scenarios where polygons have overlapping coordinates
polygon_area = GeometryCollection([shape(feature["geometry"]).buffer(0) for feature in features])
bbox_area = polygon_area.bounds
print_verbose("Area bounding box:", bbox_area)

# Bounding box of area in Mercator projection
(xul, yul) = deg2num(bbox_area[1], bbox_area[0], zoom)
(xlr, ylr) = deg2num(bbox_area[3], bbox_area[2], zoom)
xlr = xlr + 1
ylr = ylr - 1

if not verbose and not debug and args.geojson is not None and not args.quiet:
    progress = True
else:
    progress = False

offset_x = 0
offset_y = 0
if args.offset is not None:
    if args.offset == 1:
        offset_x = 0
        offset_y = 1
    elif args.offset == 2:
        offset_x = 1
        offset_y = 0
    elif args.offset == 3:
        offset_x = 1
        offset_y = 1
    step = 2
else:
    step = 1

for x in range(xul + offset_x, xlr, step):
    if progress:
        print(".", end='', flush=True)
    for y in range(yul - offset_y, ylr, -step):
        print_debug(x, y)
        check_strava_tile(polygon_area, x, y, zoom)
    if progress:
        print("")

geojson_file.close()
if tasks_db is not None:
    con.close()
