import os
import re
import sys
import math
import argparse
import logging

import numpy
import json
import cv2
from repub.imgfuncs.cropping import crop 

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
    parser.add_argument('-p', '--pagenums', nargs='*', default='pagenums', \
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


def process_image(scaninfo, infile, outfile, args):
    img = cv2.imread(infile)
    if scaninfo['rotateDegree'] == -90:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif scaninfo['rotateDegree'] == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    img = crop(img, args.xmax, args.ymax, args.maxcontours, args.drawcontours)
    cv2.imwrite(outfile, img)

def get_scandata(indir):
    scanfh = open(os.path.join(indir, 'scandata.json'), 'r', encoding = 'utf8')
    s      = scanfh.read()
    scanfh.close()
    return json.loads(s)

if __name__ == '__main__':
    parser = get_arg_parser()
    args   = parser.parse_args()

    setup_logging(args.loglevel, filename = args.logfile)
    logger = logging.getLogger('repub.main')

    indir  = args.indir 
    outdir = args.outdir

    scandata = get_scandata(indir)

    pagedata = scandata['pageData']

    for filename in os.listdir(indir):
        reobj = re.match('(?P<pagenum>\\d{4}).(jpg|jp2)$', filename)
        if reobj:
            groupdict = reobj.groupdict('pagenum')
            pagenum   = groupdict['pagenum']

            infile  = os.path.join(indir, filename)
            if re.search('.jp2$', filename):
                outfile = os.path.join(outdir, '%s.jpg' % pagenum)

            pagenum   = int(pagenum)
            pageinfo = pagedata['%d' % pagenum]
            if pageinfo['pageType'] != 'Color Card' and \
                    (len(args.pagenums) == 0 or pagenum in args.pagenums):
                logger.error ('FILEAME: %s', filename)
                process_image(pageinfo, infile, outfile, args)
