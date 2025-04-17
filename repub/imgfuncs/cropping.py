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

def get_min_max_x(vline0, vline1, columns):
    logger = logging.getLogger('repub.crop')
    minx1, maxx1 = minmax_x( vline0)
    minx2, maxx2 = minmax_x( vline1)

    logger.debug('MINX: %s',  (minx1, maxx1, minx2, maxx2, columns))
    if minx1 < minx2:
        if minx1 > columns - minx2:
            minx = maxx1
            maxx = maxx2
        else:    
            minx = minx1
            maxx = minx2
    else:
        if minx2 > columns - minx1:
            minx = maxx2
            maxx = maxx1
        else:    
            minx = minx2
            maxx = minx1

    logger.debug ('FINAL: %s', (minx, maxx))
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
        minx, maxx = get_min_max_x(vlines[0], vlines[1], img.shape[1])

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
    xwidth   = stats[2] - stats[0]
    ywidth   = stats[3] - stats[1]

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
                if box[i] == None:
                    change = True
                    box[i] = prevbox[i]

            if abs(box[0]-stats[0]) >  maxdiff and abs(box[2]-box[0] - xwidth) > 100:
                change = True
                box[0] = prevbox[0]

            if abs(box[2]-stats[2]) >  maxdiff and abs(box[2]-box[0] - xwidth) > 100:
                change = True
                box[2] = prevbox[2]

            if abs(box[1]-stats[1]) >  maxdiff and abs(box[3]-box[1] - ywidth) > 100:
                change = True
                box[1] = prevbox[1]

            if abs(box[3]-stats[3]) >  maxdiff and abs(box[3]-box[1] - ywidth) > 100:
                change = True
                box[3] = prevbox[3]

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
