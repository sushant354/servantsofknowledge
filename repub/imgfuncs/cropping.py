import cv2
import logging
import statistics

from .utils import threshold_gray, find_contour

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

def get_hvlines(contours, xmax, ymax):
    logger = logging.getLogger('repub.crop.hvlines')
    hlines = []
    vlines = []

    for contour in contours:
        prevp = None
        hline = None
        vline = None
        havg  = None
        vavg  = None

        for point in contour:
            logger.debug ('%s', point)
            x, y = point[0]
            if prevp is None:
                prevp = point[0]
                hline = [(int(x), int(y) )]
                havg = y

                vline = [(int(x), int(y))]
                vavg = x 

                hlines.append(hline)
                vlines.append(vline)
                continue

            if is_horizontal(havg, prevp, point[0], ymax):
                 l = len(hline)    
                 havg = (l * havg + y)/(l+1)
                 hline.append((int(x), int(y) ))
            else:
                logger.debug('HLINE %d %s %s', havg, prevp, point[0])
                hline = [(int(x), int(y) )]
                hlines.append(hline)
                havg = y

            if  is_vertical(vavg, prevp, point[0], xmax):
                l = len(vline)    
                vavg = (l * vavg + x)/(l+1)
                vline.append((int(x), int(y)))
            else:
                logger.debug ('VLINE %d %s %s %s', vavg, prevp, point[0], vline)
                vline = [(int(x), int(y))] 
                vlines.append(vline)
                vavg = x
            prevp = point[0]


    for hline in hlines:
        hline.sort(key = lambda x : x[0])

    for vline in vlines:
        vline.sort(key = lambda x : x[1])

    hlines.sort(key = lambda t: abs(t[-1][0] - t[0][0]), reverse = True)

    vlines.sort(key = lambda t: abs(t[-1][1] - t[0][1]), reverse = True)

    for hline in hlines[:5]:
        logger.info('HLINES %d %s', abs(hline[-1][0] - hline[0][0]), hline)

    for vline in vlines[:5]:
        logger.info('VLINES %d %s', abs(vline[-1][1] - vline[0][1]), vline)

    return hlines[:2], vlines[:2]

def is_horizontal(havg, p1, p2, ymax):
    if havg == None or abs(havg -p2[1]) < ymax:
        return True
    return False

def is_vertical(vavg, p1, p2, xmax):
    if vavg == None or abs(vavg -p2[0]) < xmax:
        return True
    return False

def get_crop_box(img, xmax, ymax, maxcontours):
    contours = find_contour(img)

    contours = contours[:maxcontours]

    hlines, vlines = get_hvlines(contours, xmax, ymax)

    minx1, maxx1 = minmax_x( vlines[0])
    minx2, maxx2 = minmax_x( vlines[1])
    if minx1 < minx2:
        minx = minx1
        maxx = maxx2
    else:
        minx = minx2
        maxx = maxx1

    miny1, maxy1 = minmax_y(hlines[0])
    miny2, maxy2 = minmax_y(hlines[1])

    if miny1 < miny2:
        miny = miny1
        maxy = maxy2
    else:
        miny = miny2
        maxy = maxy1

    logger = logging.getLogger('crop.contours')
    logger.warning('Bounding box: %s %s', (minx, miny), (maxx, maxy))

    return [minx, miny, maxx, maxy]

def fix_wrong_boxes(boxes, maxdiff, maxfirst):
    logger = logging.getLogger('repub.crop')

    pagenums = list(boxes.keys())
    pagenums.sort()

    even = [[], [], [], []]
    odd  = [[], [], [], []]

    for pagenum in pagenums:
        if pagenum % 2 == 0:
            stats = even
        else:
            stats = odd
          
        box = boxes[pagenum]
        for i in range(4):
            stats[i].append(box[i])
      
    logger.warning ('Even before stats: %s', even)
    logger.warning ('Odd before stats: %s', odd)
    for stats in [even, odd]:
        for i in range(4):
            if stats[i]:
                stats[i] = statistics.median(stats[i])
         
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
                if abs(box[i]-stats[i]) > maxdiff:# or \
                    change = True
                    box[i] = prevbox[i]

            if change:
                logger.warning('Changing cropping box for page %d from %s to %s', pagenum, prev, box)
        else:
            change = False
            prev = box.copy()
            for i in range(4):
                if abs(box[i]-stats[i]) > maxfirst:# or \
                    change = True
                    box[i] = int(stats[i])

            if change:
                logger.warning('Changing cropping box for page %d from %s to %s', pagenum, prev, box)
 
        if pagenum % 2 == 0:
            preveven = box    
        else:
            prevodd  = box


def crop(img, minx, miny, maxx, maxy):
    return img[miny:maxy, minx:maxx]

