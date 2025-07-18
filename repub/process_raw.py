import os
import re
import argparse
import logging
import sys
import shutil
import tempfile 

import json
import cv2
from repub.imgfuncs.cropping import crop, get_crop_box, fix_wrong_boxes
from repub.imgfuncs.utils import find_contour, threshold_gray
from repub.utils import pdfs, xml_ops
from repub.imgfuncs.deskew import deskew, rotate

def get_arg_parser():
    parser = argparse.ArgumentParser(description='For processing scanned book pages')
    parser.add_argument('-i', '--indir', dest='indir', action='store', \
                  required= False, help='Filepath to scanned images directory')
    parser.add_argument('-I', '--inpdf', dest='inpdf', action='store', \
                  required= False, help='Input PDF File')
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
                        help='draw contours only on the image')
    parser.add_argument('-p', '--pagenums', nargs='*', \
                        dest = 'pagenums', type=int, \
                        help = 'pagenums that should only be processed')
    parser.add_argument('-g', '--gray', action='store_true',  dest='gray', \
                        help='only gray the image and threshold it')
    parser.add_argument('-c', '--crop', action='store_true',  dest='crop', \
                        help='crop the scanned image')
    parser.add_argument('-D', '--deskew', action='store_true', \
                        dest='deskew', \
                        help='detect the skew and deskew')
    parser.add_argument('-r', '--reduce', action='store', dest='factor', \
                        required= False, type=float, \
                        help='reduce the image to factor')
    parser.add_argument('-t', '--ocr', action='store_true', dest='do_ocr', \
                        help='do ocr while making the PDF')
    parser.add_argument('-w', '--dewarp', action='store_true', \
                        dest='dewarp', help='dewarp the images')
    parser.add_argument('-H', '--outhocr', dest='outhocr', action='store', \
                       required= False, help='Output HOCR filepath')
    parser.add_argument('-T', '--outtxt', dest='outtxt', action='store', \
                       required= False, help='Output TEXT filepath')
    parser.add_argument('-N', '--thumbnail', dest='thumbnail', action='store', \
                       required= False, help='Output Thumbnail filepath')
    parser.add_argument('-A', '--iadir', dest='iadir', action='store', \
                       required= False, \
                       help='Directory for Internet Archive Full Repub')

    parser.add_argument('-R', '--rotate', action='store', default = 'vertical',\
                        dest='rotate_type', \
                        help='rotate by average of (horizontal|vertical|overall) lines')
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

    if scaninfo:
        if scaninfo['rotateDegree'] == -90:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif scaninfo['rotateDegree'] == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)

    return img    

def get_scandata(indir):
    filepath = os.path.join(indir, 'scandata.json')
    if not os.path.exists(filepath):
        return None

    scanfh = open(filepath, 'r', encoding = 'utf8')
    s      = scanfh.read()
    scanfh.close()
    return json.loads(s)

def get_metadata(indir):
    filepath = os.path.join(indir, 'metadata.xml')
    if not os.path.exists(filepath):
        return None

    metadata = xml_ops.parse_xml(filepath)
    m = {}
    for k, v in metadata.items():
        m['/%s' % k.title()] = v
    return m    

def get_scanned_pages(pagedata, indir, pagenums):
    fnames = []
    for filename in os.listdir(indir):
        reobj = re.match('(?P<pagenum>\\d{4}).(jpg|jp2)$', filename)
        if reobj:
            groupdict = reobj.groupdict('pagenum')
            pagenum   = groupdict['pagenum']

            fnames.append((filename, pagenum))

    fnames.sort(key= lambda x:x[1])

    for filename, pagenum in fnames:
        infile  = os.path.join(indir, filename)
        if re.search('.jp2$', filename):
            outfile = os.path.join(outdir, '%s.jpg' % pagenum)
        else:
            outfile = os.path.join(outdir, filename)

        pageinfo  = None
        pagenum   = int(pagenum)
        if pagedata:
            pageinfo = pagedata['%d' % pagenum]

        if (not pageinfo or pageinfo['pageType'] != 'Color Card') and \
                (not pagenums or pagenum in pagenums):
            logger.error ('FILENAME: %s', filename)
            img = read_image(pageinfo, infile) 
            yield (img, outfile, pagenum)

def draw_contours(pagedata, indir, args):        
    pagenums = args.pagenums
    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, pagenums):
        if args.deskew:
            img, hangle = deskew(img, args.xmax, args.ymax, \
                                 args.maxcontours, args.rotate_type)
        contours = find_contour(img)
        contours = contours[:args.maxcontours]

        img = cv2.drawContours(img, contours, -1, (0, 255, 0), 3)

        cv2.imwrite(outfile, img)

def gray_images(pagedata, indir, args):
    pagenums = args.pagenums
    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, pagenums):
        if args.deskew:
            img, hangle = deskew(img, args.xmax, args.ymax, args.maxcontours, args.rotate_type)
        gray = threshold_gray(img, 125, 255)
        cv2.imwrite(outfile, gray)

def deskew_images(pagedata, indir, args):
    pagenums = args.pagenums
    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, pagenums):
        deskewed, angle = deskew(img, args.xmax, args.ymax, args.maxcontours, args.rotate_type)
        cv2.imwrite(outfile, deskewed)

def resize_image(img, factor):
    (h, w) = img.shape[:2]
    width  = int(w * factor)
    height = int(h * factor)
    dim    = (width, height)
    return cv2.resize(img, dim, interpolation = cv2.INTER_AREA)

def mk_clean(outdir):
    if os.path.exists(outdir):
        shutil.rmtree(outdir)
    os.mkdir(outdir)

def get_cropping_boxes(pagedata, indir, args):
    boxes = {}
    pagenums = args.pagenums
    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, pagenums):
        img, hangle = deskew(img, args.xmax, args.ymax, args.maxcontours, args.rotate_type)
        box = get_crop_box(img, args.xmax, args.ymax, args.maxcontours)
        box.append(hangle)
        #box.append(0.0)
        boxes[pagenum] = box

    fix_wrong_boxes(boxes, 200, 250)
    return boxes

if __name__ == '__main__':
    parser = get_arg_parser()
    args   = parser.parse_args()

    setup_logging(args.loglevel, filename = args.logfile)
    logger = logging.getLogger('repub.main')

    if not args.outdir and not args.outpdf and not args.iadir:
        print ('Need either output directory for images or the pdf file for output or the InternetArchive directory', file=sys.stderr)
        parser.print_help()
        sys.exit(0)

    if not args.indir and not args.inpdf:
        print ('Need either input directory for images or the pdf file', \
               file=sys.stderr)
        parser.print_help()
        sys.exit(0)

    if  args.indir and args.inpdf:
        print ('Either specify input directory or the pdf file. Not both.', \
               file=sys.stderr)
        parser.print_help()
        sys.exit(0)

   
    indir  = args.indir 
    if args.inpdf:
        if not indir:
            indir = tempfile.mkdtemp()
        metadata = pdfs.get_metadata(args.inpdf)
        pdfs.pdf_to_images(args.inpdf, indir)
    else:
        metadata = get_metadata(indir)

    if args.iadir:
        mk_clean(args.iadir)
        outdir         = os.path.join(args.iadir, 'output')
        args.thumbnail = os.path.join(args.iadir, '__ia_thumb.jpg')
        args.outhocr   = os.path.join(args.iadir, 'x_hocr.html.gz')
        args.outtxt    = os.path.join(args.iadir, 'x_text.txt')
        args.outpdf    = os.path.join(args.iadir, 'x_final.pdf')
        os.mkdir(outdir)
        args.outdir = outdir
    elif args.outdir:
        outdir = args.outdir
        mk_clean(outdir)
    else:
        outdir = tempfile.mkdtemp()

    scandata = get_scandata(indir)

    pagedata = None
    if scandata:
        pagedata = scandata['pageData']

    if args.drawcontours:
        draw_contours(pagedata, indir, args)
        sys.exit(0)
    elif args.gray:
        gray_images(pagedata, indir, args)
        sys.exit(0)
    elif args.deskew:
        deskew_images(pagedata, indir, args)
        sys.exit(0)

    if args.crop:
        boxes = get_cropping_boxes(pagedata, indir, args)

    outfiles = []
    thumbnail = None
    for img, outfile, pagenum in get_scanned_pages(pagedata, indir, \
                                                   args.pagenums):
        if args.crop:
            box = boxes[pagenum]
            logger.warning('Bounding box for page %d: %s', pagenum, box)
            hangle = box[4]
            if hangle != None:
                img = rotate(img, hangle)
            img = crop(img, box)
            if args.dewarp:
                img = dewarp(img)

        if args.factor:
            img = resize_image(img, args.factor)
        
        if pagenum == 1 and args.thumbnail:
            thumbnail = resize_image(img, 0.1) 

        cv2.imwrite(outfile, img)
        outfiles.append((pagenum, outfile))

    if args.thumbnail:
        cv2.imwrite(args.thumbnail, thumbnail)

    if args.outpdf:
        pdfs.save_pdf(outfiles, metadata, args.langs, args.outpdf, \
                      args.do_ocr, args.outhocr, args.outtxt)

    if not args.outdir:
        shutil.rmtree(outdir)
    
    if not args.indir:
        shutil.rmtree(indir)
