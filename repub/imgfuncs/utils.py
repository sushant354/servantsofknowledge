import cv2
import logging

def threshold_gray(img, mingray, maxgray):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ret, thresh = cv2.threshold(gray, mingray, maxgray, cv2.THRESH_BINARY)
    return thresh

def find_contour(img):
    #edges = cv2.Canny(thresh, 50, 150, 3)
    thresh = threshold_gray(img, 125, 255)
    contours, hierarchy = cv2.findContours(thresh, cv2.RETR_EXTERNAL, \
                                           cv2.CHAIN_APPROX_SIMPLE )
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    return contours

def get_hvlines(contours, xmax, ymax):
    logger = logging.getLogger('repub.hvlines')
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
        logger.debug('HLINES %d %s', abs(hline[-1][0] - hline[0][0]), hline)

    for vline in vlines[:5]:
        logger.debug('VLINES %d %s', abs(vline[-1][1] - vline[0][1]), vline)

    return hlines[:2], vlines[:2]

def is_horizontal(havg, p1, p2, ymax):
    if havg == None or abs(havg -p2[1]) < ymax:
        return True
    return False

def is_vertical(vavg, p1, p2, xmax):
    if vavg == None or abs(vavg -p2[0]) < xmax:
        return True
    return False


