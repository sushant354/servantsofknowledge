import os
import re
import argparse
import logging

import json
import cv2
from repub.imgfuncs.cropping import crop, get_crop_box, find_contour

def get_arg_parser():
    parser = argparse.ArgumentParser(description='For processing scanned book pages')
    parser.add_argument('-i', '--indir', dest='indir', action='store', \
                  required= True, help='Filepath to Scanned images directory')
    parser.add_argument('-o', '--outdir', dest='outdir', action='store', \
                  required= True, help='Filepath to processed directory')
    parser.add_argument('-l', '--loglevel', dest='loglevel', action='store', \
                  default = 'info', help='debug level')
    parser.add_argument('-f', '--logfile', dest='logfile', action='store', \
                  default = None, help='log file')

    parser.add_argument('-m', '--maxcontours', type=int, dest='maxcontours',\
                        default=5, help='max number of contours to be examined')
    parser.add_argument('-x', '--xmax', type=int, dest='xmax', default=30, \
                        help='horizontal line limits  in pixels')
    parser.add_argument('-y', '--ymax', type=int, dest='ymax', default=60, \
                        help='vertical line limits in pixels')
    parser.add_argument('-d', '--drawcontours', action='store_true', \
                        dest='drawcontours', \
                        help='vertical line limits in pixels')
    parser.add_argument('-p', '--pagenums', nargs='*', \
                        dest = 'pagenums', type=int, \
                        help = 'pagenums that should only be processed')
    return parser


#logformat   = '%(asctime)s: %(name)s: %(levelname)s %(message)s'
logformat   = '%(name)s: %(message)s'
dateformat  = '%Y-%m-%d %H:%M:%S'

def initialize_file_logging(loglevel, filepath):
    logging.basicConfig(\
        level    = loglevel,  \
        format   = logformat, \
        datefmt  = dateformat, \
        stream   = filepath
    )

def initialize_stream_logging(loglevel = logging.INFO):
    logging.basicConfig(\
        level    = loglevel,  \
        format   = logformat, \
        datefmt  = dateformat \
    )


def setup_logging(level, filename = None):
    leveldict = {'critical': logging.CRITICAL, 'error': logging.ERROR, \
                 'warning': logging.WARNING,   'info': logging.INFO, \
                 'debug': logging.DEBUG}
    loglevel = leveldict[level]

    if filename:
        filestream = open(filename, 'w', encoding='utf8')
        initialize_file_logging(loglevel, filestream)
    else:
        initialize_stream_logging(loglevel)


def read_image(scaninfo, infile):
    img = cv2.imread(infile)
    if scaninfo['rotateDegree'] == -90:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif scaninfo['rotateDegree'] == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)

    return img    

def process_image(img, args):
    box = get_crop_box(img, args.xmax, args.ymax, args.maxcontours)
    return box

def get_scandata(indir):
    scanfh = open(os.path.join(indir, 'scandata.json'), 'r', encoding = 'utf8')
    s      = scanfh.read()
    scanfh.close()
    return json.loads(s)

def get_scanned_pages(pagedata, indir, pagenums):
    for filename in os.listdir(indir):
        reobj = re.match('(?P<pagenum>\\d{4}).(jpg|jp2)$', filename)
        if reobj:
            groupdict = reobj.groupdict('pagenum')
            pagenum   = groupdict['pagenum']

            infile  = os.path.join(indir, filename)
            if re.search('.jp2$', filename):
                outfile = os.path.join(outdir, '%s.jpg' % pagenum)
            else:
                outfile = os.path.join(outdir, filename)

            pagenum   = int(pagenum)
            pageinfo = pagedata['%d' % pagenum]
            if pageinfo['pageType'] != 'Color Card' and \
                    (not pagenums or pagenum in pagenums):
                logger.error ('FILEAME: %s', filename)
                img = read_image(pageinfo, infile) 
                yield (img, outfile, pagenum)

if __name__ == '__main__':
    parser = get_arg_parser()
    args   = parser.parse_args()

    setup_logging(args.loglevel, filename = args.logfile)
    logger = logging.getLogger('repub.main')

    indir  = args.indir 
    outdir = args.outdir

    scandata = get_scandata(indir)

    pagedata = scandata['pageData']

    boxes = {}

    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, \
                                                   args.pagenums):
        if args.drawcontours:
            contours = find_contour(img)
            contours = contours[:args.maxcontours]

            img = cv2.drawContours(img, contours, -1, (0, 255, 0), 3)

            cv2.imwrite(outfile, img)
        else:
            box = process_image(img,  args)
            boxes[pagenum] = box

    pagenums = list(boxes.keys())
    pagenums.sort()

    even = {'minx': 0 , 'miny': 0, 'maxx': 0, 'maxy': 0, 'count': 0}
    odd  = {'minx': 0 , 'miny': 0, 'maxx': 0, 'maxy': 0, 'count': 0}

    for pagenum in pagenums:
        if pagenum % 2 == 0:
            stats = even
        else:
            stats = odd
          
        box = boxes[pagenum]
        stats['minx']  += box[0]
        stats['miny']  += box[1]
        stats['maxx']  += box[2]
        stats['maxy']  += box[3]
        stats['count'] += 1
      
    for stats in [even, odd]:
        stats['minx'] /= stats['count']
        stats['miny'] /= stats['count']
        stats['maxx'] /= stats['count']
        stats['maxy'] /= stats['count']
         
    logger.warning ('Even: ', even)
    logger.warning ('Odd: ', odd)

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
            if abs(box[0]-stats['minx']) > 0.1 * stats['minx']:
                change = True
                box[0] = prevbox[0]

            if abs(box[1]-stats['miny']) > 0.1 * stats['miny']:
                change = True
                box[1] = prevbox[1]

            if abs(box[2]-stats['maxx']) > 0.1 * stats['maxx']:
                change = True
                box[2] = prevbox[2]

            if abs(box[3]-stats['maxy']) > 0.1 * stats['maxy']:
                change = True
                box[3] = prevbox[3]

            if change:
                logger.warning('Changing cropping box for page %d from %s to %s', pagenum, prev, box)

        if pagenum % 2 == 0:
            preveven = box    
        else:
            prevodd  = box

    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, \
                                                    args.pagenums):
        box = boxes[pagenum]
        img = crop(img, box[0], box[1], box[2], box[3])
        cv2.imwrite(outfile, img)
