import cv2
import logging
import statistics
import math

from .utils import  find_contour, get_hvlines

def minmax_x (line):
    maxx = 0
    minx = None

    for x, y in line:
        if minx == None:
            minx = x
        else:
            if x < minx:
                minx = x
        if maxx < x:
            maxx = x
    return minx, maxx

def minmax_y (line):
    maxy = 0
    miny = None

    for x, y in line:
        if miny == None:
            miny = y
        else:
            if y < miny:
                miny = y
        if maxy < y:
            maxy = y
    return miny, maxy

def get_min_max_x(vline0, vline1):
    minx1, maxx1 = minmax_x( vline0)
    minx2, maxx2 = minmax_x( vline1)
    if minx1 < minx2:
        minx = maxx1
        maxx = minx2
    else:
        minx = maxx2
        maxx = minx1

    return minx, maxx

def get_min_max_y(hline0, hline1):
    miny1, maxy1 = minmax_y(hline0)
    miny2, maxy2 = minmax_y(hline1)

    if miny1 < miny2:
        miny = maxy1
        maxy = miny2
    else:
        miny = maxy2
        maxy = miny1

    return miny, maxy

def get_crop_box(img, xmax, ymax, maxcontours):
    logger = logging.getLogger('repub.crop')
    contours = find_contour(img)

    contours = contours[:maxcontours]

    hlines, vlines = get_hvlines(contours, xmax, ymax)

    minx = maxx = miny = maxy = None

    if len(vlines) >= 2:
        minx, maxx = get_min_max_x(vlines[0], vlines[1])

    if len(hlines) >= 2:
        miny, maxy = get_min_max_y(hlines[0], hlines[1])

    logger.warning('Bounding box: %s %s', (minx, miny), (maxx, maxy))
    
    return [minx, miny, maxx, maxy]

def fix_wrong_boxes(boxes, maxdiff, maxfirst):
    logger = logging.getLogger('repub.crop')

    pagenums = list(boxes.keys())
    pagenums.sort()

    even = [[], [], [], [], []]
    odd  = [[], [], [], [], []]

    for pagenum in pagenums:
        if pagenum % 2 == 0:
            stats = even
        else:
            stats = odd
          
        box = boxes[pagenum]
        for i in range(5):
            if box[i] != None:
                stats[i].append(box[i])
      
    for stats in [even, odd]:
        for i in range(5):
            if stats[i]:
                stats[i] = statistics.median(stats[i])
                if i < 4:
                    stats[i] = int(stats[i])
         
    logger.warning ('Even: %s', even)
    logger.warning ('Odd: %s', odd)

    preveven = None
    prevodd  = None
    for pagenum in pagenums:
        box = boxes[pagenum]
        if pagenum % 2 == 0:
            stats = even
            prevbox = preveven
        else:
            stats = odd
            prevbox = prevodd

        if prevbox!= None:
            change = False
            prev = box.copy()
            for i in range(4):
                if box[i] == None or abs(box[i]-stats[i]) > maxdiff:# or \
                    change = True
                    box[i] = prevbox[i]

            if change:
                logger.warning('Changing cropping box for page %d from %s to %s', pagenum, prev, box)
                box[4] = stats[4]
        else:
            change = False
            prev = box.copy()
            for i in range(4):
                if box[i] == None or abs(box[i]-stats[i]) > maxfirst:# or \
                    change = True
                    box[i] = stats[i]

            if change:
                logger.warning('Cropping box replaced for page %d from %s to %s', pagenum, prev, box)
                box[4] = stats[4]
        if pagenum % 2 == 0:
            preveven = box    
        else:
            prevodd  = box

def crop(img, box):
    minx = box[0]
    miny = box[1] 
    maxx = box[2]
    maxy = box[3]

    return img[miny:maxy, minx:maxx]
