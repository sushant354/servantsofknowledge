import cv2
import logging

def find_contour(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ret, thresh = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY)
    #edges = cv2.Canny(thresh, 50, 150, 3)

    contours, hierarchy = cv2.findContours(thresh, cv2.RETR_EXTERNAL, \
                                           cv2.CHAIN_APPROX_SIMPLE )
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    return contours

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
    logger = logging.getLogger('crop.hvlines')
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
            if prevp is None:
                prevp = point[0]
                continue

            x, y = point[0]
            if is_horizontal(havg, prevp, point[0], ymax):
                if not hline:
                    hline = []
                    hlines.append(hline)
                    havg = y

                l = len(hline)    
                havg = (l * havg + y)/(l+1)
                hline.append((int(x), int(y) ))
            else:
                logger.debug('HLINE %d %s %s', havg, prevp, point[0])
                hline = None
                havg = None

            if  is_vertical(vavg, prevp, point[0], xmax):
                if not vline:
                    vline = []
                    vlines.append(vline)
                    vavg = x 

                l = len(vline)    
                vavg = (l * vavg + x)/(l+1)
                vline.append((int(x), int(y)))
            else:
                logger.debug ('VLINE %d %s %s', vavg, prevp, point[0])
                vline = None
                vavg = None
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

def crop(img, xmax, ymax, maxcontours, drawcontours):
    contours = find_contour(img)

    contours = contours[:maxcontours]

    if drawcontours:
        return cv2.drawContours(img, contours, -1, (0, 255, 0), 3) 

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

    if minx > 500:
        minx = 0
    logger = logging.getLogger('crop.contours')
    logger.warning('Bounding box: %s %s', (minx, miny), (maxx, maxy))
    return img[miny:maxy, minx:maxx]
