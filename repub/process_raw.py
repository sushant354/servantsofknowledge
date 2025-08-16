import os
import argparse
import logging
import sys
import shutil
import tempfile 

import cv2
from repub.imgfuncs.cropping import crop, get_crop_box, fix_wrong_boxes
from repub.imgfuncs.utils import find_contour, threshold_gray
from repub.utils import pdfs
from repub.imgfuncs.deskew import deskew, rotate
from repub.utils import utils
from repub.utils.scandir import Scandir

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


def draw_contours(scandir, args):        
    outfiles = []
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        if args.deskew:
            img, hangle = deskew(img, args.xmax, args.ymax, \
                                 args.maxcontours, args.rotate_type)
        contours = find_contour(img)
        contours = contours[:args.maxcontours]

        img = cv2.drawContours(img, contours, -1, (0, 255, 0), 3)

        cv2.imwrite(outfile, img)
        outfiles.append((pagenum, outfile))
    return outfiles

def gray_images(scandir, args):
    outfiles = []
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        if args.deskew:
            img, hangle = deskew(img, args.xmax, args.ymax, args.maxcontours, args.rotate_type)
        gray = threshold_gray(img, 125, 255)
        cv2.imwrite(outfile, gray)
        outfiles.append((pagenum, outfile))
    return outfiles

def deskew_images(scandir,  args):
    outfiles = []
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        deskewed, angle = deskew(img, args.xmax, args.ymax, args.maxcontours, args.rotate_type)
        cv2.imwrite(outfile, deskewed)
        outfiles.append((pagenum, outfile))

    return outfiles

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

def get_cropping_boxes(scandir, args):
    boxes = {}
    for img, infile, outfile, pagenum in scandir.get_scanned_pages():
        img, hangle = deskew(img, args.xmax, args.ymax, args.maxcontours, args.rotate_type)
        box = get_crop_box(img, args.xmax, args.ymax, args.maxcontours)
        box.append(hangle)
        #box.append(0.0)
        boxes[pagenum] = box

    fix_wrong_boxes(boxes, 200, 250)
    return boxes

def process_images(scandir, args):
    logger = logging.getLogger('repub.main')
    if args.crop:
        boxes = get_cropping_boxes(scandir, args)

    outfiles = []
    thumbnail = None
    for img, infile, outfile, pagenum in scandir.get_scanned_pages(): 
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
        
        if args.thumbnail and thumbnail is None and scandir.is_cover_page(pagenum):
            thumbnail = resize_image(img, 0.1) 

        cv2.imwrite(outfile, img)
        outfiles.append((pagenum, outfile))

    if thumbnail is not None:
        cv2.imwrite(args.thumbnail, thumbnail)

    return outfiles

def initialize_iadir(args):
    mk_clean(args.iadir)
    outdir         = os.path.join(args.iadir, 'output')
    args.thumbnail = os.path.join(args.iadir, '__ia_thumb.jpg')
    args.outhocr   = os.path.join(args.iadir, 'x_hocr.html.gz')
    args.outtxt    = os.path.join(args.iadir, 'x_text.txt')
    args.outpdf    = os.path.join(args.iadir, 'x_final.pdf')
    os.mkdir(outdir)
    args.outdir = outdir
    args.crop = True
    args.do_ocr = True

if __name__ == '__main__':
    parser = get_arg_parser()
    args   = parser.parse_args()

    utils.setup_logging(args.loglevel, filename = args.logfile)
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

    if args.iadir:
        initialize_iadir(args)
        outdir = args.outdir
    elif args.outdir:
        outdir = args.outdir
        mk_clean(outdir)
    else:
        outdir = tempfile.mkdtemp()

    scandir = Scandir(indir, outdir, args.pagenums)
    if not args.inpdf:
        metadata = scandir.metadata

    if args.drawcontours:
        draw_contours(scandir, args)
        sys.exit(0)
    elif args.gray:
        gray_images(scandir, args)
        sys.exit(0)
    elif args.deskew:
        deskew_images(scandir, args)
        sys.exit(0)

    outfiles = process_images(scandir, args)
    if args.outpdf:
        pdfs.save_pdf(outfiles, metadata, args.langs, args.outpdf, \
                      args.do_ocr, args.outhocr, args.outtxt)

    if not args.outdir:
        shutil.rmtree(outdir)
    
    if not args.indir:
        shutil.rmtree(indir)
