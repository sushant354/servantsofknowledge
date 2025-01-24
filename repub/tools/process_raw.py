import os
import re
import argparse
import logging
import statistics 
import sys
import pytesseract
import PyPDF2
import io
import shutil

import json
import cv2
from repub.imgfuncs.cropping import crop, get_crop_box, find_contour, threshold_gray

def get_arg_parser():
    parser = argparse.ArgumentParser(description='For processing scanned book pages')
    parser.add_argument('-i', '--indir', dest='indir', action='store', \
                  required= True, help='Filepath to Scanned images directory')
    parser.add_argument('-o', '--outdir', dest='outdir', action='store', \
                  required= False, help='Filepath to processed directory')
    parser.add_argument('-O', '--outpdf', dest='outpdf', action='store', \
                  required= False, help='Output PDF filepath')
    parser.add_argument('-l', '--loglevel', dest='loglevel', action='store', \
                  default = 'info', help='debug level')
    parser.add_argument('-L', '--language', dest='langs', action='store', \
                  default = 'eng', help='language for tesseract')
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
    parser.add_argument('-g', '--gray', action='store_true',  dest='gray', \
                        help='only gray the image and threshold it')
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

def fix_wrong_boxes(boxes, maxdiff, maxfirst):
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

def save_pdf(outfiles, langs, outpdf):
    outfiles.sort(key = lambda x: x[1])
    pdf_writer = PyPDF2.PdfWriter()
    # export the searchable PDF to searchable.pdf
    for pagenum, outfile in outfiles:
        page = pytesseract.image_to_pdf_or_hocr(outfile, extension='pdf', lang =langs)
        pdf = PyPDF2.PdfReader(io.BytesIO(page))
        pdf_writer.add_page(pdf.pages[0])

    with open(outpdf, "wb") as f:
        pdf_writer.write(f)    

if __name__ == '__main__':
    parser = get_arg_parser()
    args   = parser.parse_args()

    setup_logging(args.loglevel, filename = args.logfile)
    logger = logging.getLogger('repub.main')

    if not args.outdir and not args.outpdf:
        print ('Need either output directory for images or the pdf file for output', file=sys.stderr)
        parser.print_help()
        sys.exit(0)

    indir  = args.indir 

    if args.outdir:
        outdir = args.outdir
    else:
        outdir = tempfile.mkdtemp()

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
        elif args.gray:
            gray = threshold_gray(img, 125, 255)
            cv2.imwrite(outfile, gray)
        else:
            box = process_image(img,  args)
            boxes[pagenum] = box

    if args.drawcontours or args.gray:
        sys.exit(0)

    fix_wrong_boxes(boxes, 200, 250)
    outfiles = []
    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, \
                                                   args.pagenums):
        box = boxes[pagenum]
        logger.warning('Bounding box for page %d: %s', pagenum, box)
        img = crop(img, box[0], box[1], box[2], box[3])
        cv2.imwrite(outfile, img)
        outfiles.append((pagenum, outfile))

    if args.outpdf:
        save_pdf(outfiles, args.langs, args.outpdf)

    if not args.outdir:
        shutil.rmtree(outdir)
